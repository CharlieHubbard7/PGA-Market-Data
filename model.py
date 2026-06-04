"""
Model win-probability feed — DataGolf (paid API).

Pulls each player's model win probability and joins it to the Kalshi field, so
the dashboard can show a "Model" price next to the market's True price and a live
delta (edge) between them. Mirrors leaderboard.py / form.py:
  - refresh_model_loop(): polls DataGolf every MODEL_REFRESH_SECONDS, annotates
    feed.markets["...model"] in place, and broadcasts a per-ticker map
    (type: "model"). The browser just applies what we send.

KEY: put your DataGolf key in .env as
    DATAGOLF_API_KEY=xxxxxxxxxxxxxxxx
Without a key this feed no-ops cleanly (model shows "—"); nothing else breaks.

A model win probability is a 0–1 number, which IS a price on a 0–1 contract:
0.185 → 18.5¢ ("move the decimal"). We store it as a 0–1 fraction so it formats
exactly like the K/P/True prices.

────────────────────────────────────────────────────────────────────────────────
SWAP NOTE: everything DataGolf-specific lives in _fetch_raw() + _parse() below.
If you use a different model API, change ONLY those two functions to return
{normalized_player_name: win_probability_0_to_1}. The loop, matching, broadcast,
DB, and dashboard are all provider-agnostic.
────────────────────────────────────────────────────────────────────────────────

Standalone:  python3 model.py     # one fetch, prints the model board (needs a key)
"""

import asyncio
import time

import requests

import config
import db
import feed
from leaderboard import normalize_name, _fuzzy_key

# DataGolf endpoints. In-play (live, during a round) is preferred; we fall back to
# pre-tournament when there's no live data (before/between rounds).
DG_BASE      = "https://feeds.datagolf.com"
DG_IN_PLAY   = "/preds/in-play"
DG_PRE       = "/preds/pre-tournament"
MODEL_REFRESH_SECONDS = 120     # the user wants a freshness clock; refresh ~2 min
TIMEOUT = 20

# ── shared state (read by server.py) ────────────────────────────────────────────
# model_by_ticker: Kalshi ticker -> {"prob": 0-1 float, "updated": epoch seconds}
model_by_ticker: dict[str, dict] = {}
last_updated: float | None = None     # epoch of the last successful fetch

# Optional async hook set by server.py: await on_update() after each refresh.
on_update = None


# ── DataGolf fetch + parse (the only provider-specific code) ─────────────────────

def _fetch_raw() -> dict | None:
    """Hit DataGolf. Try live in-play first, then pre-tournament. Returns the raw
    JSON dict, or None if there is no key / nothing usable."""
    if not config.DATAGOLF_API_KEY:
        return None
    params = {
        "tour": "pga",
        "odds_format": "percent",   # win comes back as a percentage (18.5 = 18.5%)
        "file_format": "json",
        "key": config.DATAGOLF_API_KEY,
    }
    for path in (DG_IN_PLAY, DG_PRE):
        try:
            r = requests.get(f"{DG_BASE}{path}", params=params, timeout=TIMEOUT)
            if r.status_code != 200:
                continue
            j = r.json()
            if _player_list(j):       # only use it if it actually has players
                return j
        except Exception as e:
            print(f"[model] fetch error on {path}: {e}", flush=True)
    return None


def _player_list(j: dict) -> list:
    """DataGolf nests the player array under different keys across endpoints
    (in-play uses 'data'; pre-tournament uses 'baseline'). Find whichever exists."""
    if not isinstance(j, dict):
        return j if isinstance(j, list) else []
    for key in ("data", "baseline", "baseline_history_fit", "players"):
        v = j.get(key)
        if isinstance(v, list) and v:
            return v
    return []


def _flip_name(name: str) -> str:
    """DataGolf returns 'Scheffler, Scottie' → 'Scottie Scheffler' for matching."""
    if "," in name:
        last, first = name.split(",", 1)
        return f"{first.strip()} {last.strip()}"
    return name


def _to_prob(win) -> float | None:
    """win may be a percent (18.5) or a fraction (0.185); normalize to 0–1."""
    try:
        v = float(win)
    except (TypeError, ValueError):
        return None
    if v < 0:
        return None
    return v / 100.0 if v > 1.0 else v      # >1 means it's a percentage


def _parse(j: dict) -> dict:
    """raw DataGolf JSON → {normalized player name: win_prob_0_to_1}."""
    out = {}
    for p in _player_list(j):
        if not isinstance(p, dict):
            continue
        name = p.get("player_name") or p.get("name")
        win  = p.get("win")
        if name is None or win is None:
            continue
        prob = _to_prob(win)
        if prob is not None:
            out[normalize_name(_flip_name(name))] = prob
    return out


# ── join to the Kalshi field + refresh loop ──────────────────────────────────────

def _apply(prob_by_name: dict) -> dict:
    """Map model probs onto Kalshi tickers (reusing the leaderboard fuzzy matcher).
    Annotates feed.markets in place; returns the per-ticker wire map."""
    by_key = {}
    for nm, prob in prob_by_name.items():
        parts = nm.split()
        if len(parts) >= 2:
            by_key.setdefault((parts[-1], parts[0][0]), prob)

    now = time.time()
    updates = {}
    for ticker, m in feed.markets.items():
        prob = prob_by_name.get(normalize_name(m["player"]))
        if prob is None:
            k = _fuzzy_key(m["player"])
            prob = by_key.get(k) if k else None
        if prob is None:
            continue
        rec = {"prob": prob, "updated": now}
        m["model"] = rec
        updates[ticker] = rec
    return updates


async def refresh_model_loop():
    global last_updated
    while True:
        try:
            if not config.DATAGOLF_API_KEY:
                print("[model] no DATAGOLF_API_KEY set — model column disabled", flush=True)
                await asyncio.sleep(MODEL_REFRESH_SECONDS)
                continue
            raw = await asyncio.to_thread(_fetch_raw)
            probs = _parse(raw) if raw else {}
            if probs:
                updates = _apply(probs)
                model_by_ticker.clear()
                model_by_ticker.update(updates)
                last_updated = time.time()
                db.write_model(updates, feed.markets)
                if on_update:
                    try:
                        await on_update()
                    except Exception as e:
                        print(f"[model] on_update hook error: {e}", flush=True)
                print(f"[model] {len(probs)} model probs — matched {len(updates)}/{len(feed.markets)} markets", flush=True)
            else:
                print("[model] no usable model data this cycle", flush=True)
        except Exception as e:
            print(f"[model] refresh error: {e}", flush=True)
        await asyncio.sleep(MODEL_REFRESH_SECONDS)


# ── standalone ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not config.DATAGOLF_API_KEY:
        print("Set DATAGOLF_API_KEY in .env first.")
    else:
        raw = _fetch_raw()
        probs = _parse(raw) if raw else {}
        print(f"{len(probs)} model probabilities\n")
        for nm, pr in sorted(probs.items(), key=lambda kv: -kv[1])[:20]:
            print(f"  {pr*100:5.1f}c  {nm}")
