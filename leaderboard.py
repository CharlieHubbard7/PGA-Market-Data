import asyncio
import re
import unicodedata

import requests

import db
import feed

ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard"
POLL_SECONDS = 30   # ESPN updates live scores ~continuously; 30s is a safe, responsive cadence

# ── shared state (read by server.py) ───────────────────────────────────────────
# board:   ordered list of leaderboard rows (low score first)
# by_name: normalized player name → row (exact-match index)
# by_key:  (last name, first initial) → row (fuzzy fallback index)
board:   list[dict] = []
by_name: dict[str, dict] = {}
by_key:  dict[tuple[str, str], dict] = {}

# Letters that ascii-fold to nothing under NFKD must be transliterated by hand
# (e.g. Højgaard → hojgaard, not hjgaard).
_TRANSLIT = str.maketrans({
    "ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "å": "a", "Å": "a",
    "ł": "l", "Ł": "l", "ð": "d", "Ð": "d", "þ": "th", "Þ": "th",
})


# ── name matching ───────────────────────────────────────────────────────────────

def normalize_name(name: str) -> str:
    """Canonical key for matching ESPN ↔ Kalshi player names.

    Transliterates special latin letters, strips accents, lowercases, drops
    punctuation (J.T. → jt), collapses spaces.
    """
    if not name:
        return ""
    name = name.translate(_TRANSLIT)
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = name.lower()
    name = re.sub(r"[^a-z0-9 ]", "", name)   # drop periods, hyphens, apostrophes
    return re.sub(r"\s+", " ", name).strip()


def _fuzzy_key(name: str) -> tuple[str, str] | None:
    """(last name, first initial) — tolerant of Matt↔Matthew, Nico↔Nicolas."""
    parts = normalize_name(name).split()
    if len(parts) < 2:
        return None
    return (parts[-1], parts[0][0])


def match(player_name: str) -> dict | None:
    """Resolve a Kalshi player name to a leaderboard row. Exact norm first,
    then last-name + first-initial fuzzy fallback."""
    row = by_name.get(normalize_name(player_name))
    if row:
        return row
    key = _fuzzy_key(player_name)
    return by_key.get(key) if key else None


# ── ESPN parsing ──────────────────────────────────────────────────────────────

def _score_val(s: str | None) -> int:
    """Sortable numeric value for a golf score string ('E', '-3', '+1')."""
    if not s:
        return 999
    s = s.strip().upper()
    if s == "E":
        return 0
    try:
        return int(s.replace("+", ""))
    except ValueError:
        return 999


def _thru(competitor: dict) -> str:
    """Holes played in the most recent started round → '12', 'F', or '—'."""
    for period in reversed(competitor.get("linescores", [])):
        holes = period.get("linescores", [])
        if holes:
            return "F" if len(holes) >= 18 else str(len(holes))
    return "—"


def _parse(payload: dict) -> list[dict]:
    events = payload.get("events", [])
    if not events:
        return []
    comps = events[0].get("competitions", [])
    if not comps:
        return []
    competitors = comps[0].get("competitors", [])

    # Sort by score so position/tie logic is independent of ESPN's ordering.
    ranked = sorted(competitors, key=lambda c: _score_val(c.get("score")))

    rows = []
    for c in ranked:
        name  = c.get("athlete", {}).get("displayName", "")
        score = c.get("score") or "—"
        val   = _score_val(c.get("score"))

        # Tie handling: rank = #players strictly ahead + 1; 'T' if shared.
        ahead = sum(1 for o in ranked if _score_val(o.get("score")) < val)
        shared = sum(1 for o in ranked if _score_val(o.get("score")) == val)
        position = ("T" if shared > 1 else "") + str(ahead + 1) if val != 999 else "—"

        rows.append({
            "player_name": name,
            "norm":        normalize_name(name),
            "position":    position,
            "total":       score,
            "thru":        _thru(c),
        })
    return rows


# ── poll loop ───────────────────────────────────────────────────────────────────

async def refresh_leaderboard_loop():
    while True:
        try:
            resp = await asyncio.to_thread(requests.get, ESPN_URL, timeout=10)
            resp.raise_for_status()
            rows = _parse(resp.json())

            if rows:
                board.clear()
                board.extend(rows)
                by_name.clear()
                by_name.update({r["norm"]: r for r in rows})
                by_key.clear()
                for r in rows:
                    k = _fuzzy_key(r["player_name"])
                    if k:
                        by_key.setdefault(k, r)   # first (best-ranked) wins on collision
                db.write_leaderboard(rows)

                # Join onto Kalshi markets and broadcast a per-ticker update map.
                updates = {}
                for ticker, m in feed.markets.items():
                    row = match(m["player"])
                    m["pos"]   = row["position"] if row else "—"
                    m["score"] = row["total"]    if row else "—"
                    m["thru"]  = row["thru"]     if row else "—"
                    updates[ticker] = {"pos": m["pos"], "score": m["score"], "thru": m["thru"]}

                matched = sum(1 for u in updates.values() if u["pos"] != "—")
                await feed._broadcast({"type": "leaderboard", "markets": updates})
                print(f"[leaderboard] {len(rows)} players — matched {matched}/{len(updates)} markets")
        except Exception as e:
            print(f"[leaderboard] refresh error: {e}")
        await asyncio.sleep(POLL_SECONDS)
