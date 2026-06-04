"""
Phase 3.6 — player recent-form data (ESPN, free, no auth).

Mirrors leaderboard.py's shape: a background poll loop that fetches per-player
form from ESPN, annotates feed.markets in place, and broadcasts a per-ticker map
(type: "form"). The browser just applies what we send — it never fetches ESPN.

Data (validated in form_probe.py — 72/72 coverage on the live field):
  - source of athlete ids: the PGA scoreboard competitor `id` IS the ESPN athlete
    id, and the scoreboard = the live field, so every player maps for free.
  - one endpoint per player: /athletes/{id}/overview →
      recentTournaments.eventsStats  → last-5 finishes (L1 / avg-L3 / avg-L5)
      statistics.splits (PGA TOUR)   → events / cuts / top10 / wins / scoring avg
      seasonRankings.categories      → driving dist+acc, GIR, putts/GIR, birdies/
                                       rnd, money rank (free Strokes-Gained proxies)
  - field z-scores: scoring-avg & avg-L5-finish (inverted so HIGH = good form);
    form_score = mean of the available z's. Strokes Gained / OWGR are NOT free.

Refresh is slow on purpose — form changes between tournaments, not minute to
minute. We exclude the in-progress event so L1/L3/L5 are completed starts only.

Standalone:  python3 form.py        # fetch the field + print the form board
"""

import asyncio
import statistics

import requests

import db
import feed
from leaderboard import normalize_name, _fuzzy_key

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard"
OVERVIEW   = "https://site.web.api.espn.com/apis/common/v3/sports/golf/pga/athletes/{id}/overview"
REFRESH_SECONDS = 6 * 3600   # form moves between events; 6h is plenty
TIMEOUT = 15

# seasonRankings category → our label. These carry a season VALUE + a PGA-Tour rank.
# (ESPN has no true Strokes Gained; these are the free season skill stats.)
RANK_CATS = {
    "yardsPerDrive":    "drive_dist",   # driving distance (yds)
    "driveAccuracyPct": "drive_acc",    # driving accuracy %
    "puttsGirAvg":      "putts_gir",    # putts per GIR (putting)
    "birdiesPerRound":  "birdies_rnd",  # birdies per round
    "sandSaves":        "sand_save",    # sand save %
}

# ── shared state (read by server.py) ────────────────────────────────────────────
# form_by_ticker: Kalshi ticker → form dict (the wire shape the dashboard gets)
form_by_ticker: dict[str, dict] = {}

# Optional async hook set by server.py: await on_update() after each refresh.
on_update = None


# ── ESPN fetch + parse (promoted from form_probe.py) ─────────────────────────────

def _fetch_field() -> tuple[list[dict], str | None]:
    d = requests.get(SCOREBOARD, timeout=TIMEOUT).json()
    comps = d["events"][0]["competitions"][0]["competitors"]
    # the live in-progress event id — exclude it from "recent finishes"
    cur_event = d["events"][0].get("id")
    out = []
    for c in comps:
        aid, name = c.get("id"), c.get("athlete", {}).get("displayName", "")
        if aid and name:
            out.append({"athlete_id": aid, "name": name})
    return out, cur_event


def _finish_num(disp):
    if not disp:
        return None
    s = str(disp).strip().upper().lstrip("T")
    return int(s) if s.isdigit() else None


def _num(x):
    try:
        return float(str(x).replace(",", "").replace("$", ""))
    except (ValueError, TypeError):
        return None


def _recent_finishes(ov: dict, cur_event: str | None) -> list[int]:
    """Completed finish positions, newest first — current event excluded."""
    rt = ov.get("recentTournaments") or []
    if not rt:
        return []
    out = []
    for e in rt[0].get("eventsStats", []):
        if cur_event and str(e.get("id")) == str(cur_event):
            continue
        comps = e.get("competitions") or []
        cps = comps[0].get("competitors") if comps else None
        if not cps:
            continue
        st = cps[0].get("status", {})
        if (st.get("type") or {}).get("state") != "post":
            continue            # only completed events
        n = _finish_num((st.get("position") or {}).get("displayName"))
        if n is not None:
            out.append(n)
    return out


def _season_splits(ov: dict) -> dict:
    st = ov.get("statistics", {})
    labels = st.get("labels", [])
    for sp in st.get("splits", []):
        if sp.get("displayName") == "PGA TOUR":
            v = dict(zip(labels, sp.get("stats", [])))
            return {
                "events":      _num(v.get("EVENTS")),
                "cuts":        _num(v.get("CUTS")),
                "top10":       _num(v.get("TOP10")),
                "wins":        _num(v.get("WINS")),
                "scoring_avg": _num(v.get("AVG")),
            }
    return {}


def _skills(ov: dict) -> dict:
    """Season skill stats → {label: {'value': float|None, 'rank': int|None}},
    plus a flat 'earnings_rank'. The values are the actual season numbers
    (302 yds, 64.8% etc.); ranks are PGA-Tour-wide (lower = better)."""
    out = {}
    for c in (ov.get("seasonRankings") or {}).get("categories", []):
        nm = c.get("name")
        if nm in RANK_CATS:
            out[RANK_CATS[nm]] = {"value": c.get("value"), "rank": c.get("rank")}
        elif nm == "amount":
            out["earnings_rank"] = c.get("rank")
    return out


def _recent_gir(ov: dict, cur_event) -> float | None:
    """Avg GIR% over recent completed events — ESPN exposes GIR per-tournament
    (the season 'greensHit' category is a raw count, not a %), so we average it."""
    rt = ov.get("recentTournaments") or []
    if not rt:
        return None
    vals = []
    for e in rt[0].get("eventsStats", []):
        if cur_event and str(e.get("id")) == str(cur_event):
            continue
        cps = (e.get("competitions") or [{}])[0].get("competitors") or []
        if not cps:
            continue
        if (cps[0].get("status", {}).get("type") or {}).get("state") != "post":
            continue
        stats = {s.get("abbreviation"): s.get("displayValue") for s in cps[0].get("stats", [])}
        g = _num(stats.get("GIR"))
        if g is not None:
            vals.append(g)
    return round(statistics.mean(vals), 1) if vals else None


def _fetch_one(player: dict, cur_event: str | None) -> dict:
    row = {"name": player["name"], "athlete_id": player["athlete_id"]}
    try:
        ov = requests.get(OVERVIEW.format(id=player["athlete_id"]), timeout=TIMEOUT).json()
    except Exception as e:
        row["error"] = str(e)[:60]
        return row
    fins = _recent_finishes(ov, cur_event)
    sp = _season_splits(ov)
    row.update({
        "last1":  fins[0] if fins else None,
        "avg_l3": round(statistics.mean(fins[:3]), 1) if fins else None,
        "avg_l5": round(statistics.mean(fins[:5]), 1) if fins else None,
        "scoring_avg": sp.get("scoring_avg"),
        "top10": sp.get("top10"), "wins": sp.get("wins"), "events": sp.get("events"),
        "made_cut_pct": round(100 * sp["cuts"] / sp["events"]) if sp.get("events") else None,
        "skills": _skills(ov),
        "gir_pct": _recent_gir(ov, cur_event),
    })
    return row


def _zscore(rows, field, invert=False):
    vals = [r[field] for r in rows if r.get(field) is not None]
    if len(vals) < 3:
        return
    mu, sd = statistics.mean(vals), statistics.pstdev(vals)
    if sd == 0:
        return
    for r in rows:
        if r.get(field) is not None:
            z = (r[field] - mu) / sd
            r[f"{field}_z"] = round(-z if invert else z, 2)


def build_form() -> list[dict]:
    """Fetch + parse + z-score the whole field. Synchronous; call via to_thread."""
    field, cur_event = _fetch_field()
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        rows = list(ex.map(lambda p: _fetch_one(p, cur_event), field))
    rows = [r for r in rows if "error" not in r]

    _zscore(rows, "scoring_avg", invert=True)   # lower avg = better
    _zscore(rows, "avg_l5",      invert=True)    # lower finish = better
    for r in rows:
        zs = [r[k] for k in ("avg_l5_z", "scoring_avg_z") if r.get(k) is not None]
        r["form_score"] = round(statistics.mean(zs), 2) if zs else None
    return rows


# ── poll loop ────────────────────────────────────────────────────────────────────

def _apply(rows: list[dict]) -> dict:
    """Map form rows onto Kalshi tickers (reusing leaderboard fuzzy match) and
    annotate feed.markets in place. Returns the per-ticker wire map."""
    by_norm = {normalize_name(r["name"]): r for r in rows}
    by_key  = {}
    for r in rows:
        k = _fuzzy_key(r["name"])
        if k:
            by_key.setdefault(k, r)

    updates = {}
    for ticker, m in feed.markets.items():
        r = by_norm.get(normalize_name(m["player"]))
        if not r:
            k = _fuzzy_key(m["player"])
            r = by_key.get(k) if k else None
        if not r:
            continue
        wire = {
            "form_score":   r.get("form_score"),
            "avg_l5":       r.get("avg_l5"),
            "avg_l3":       r.get("avg_l3"),
            "last1":        r.get("last1"),
            "made_cut_pct": r.get("made_cut_pct"),
            "scoring_avg":  r.get("scoring_avg"),
            "top10":        r.get("top10"),
            "wins":         r.get("wins"),
            "gir_pct":      r.get("gir_pct"),
            "skills":       r.get("skills", {}),   # {drive_dist:{value,rank}, …}
        }
        m["form"] = wire
        updates[ticker] = wire
    return updates


async def refresh_form_loop():
    while True:
        try:
            rows = await asyncio.to_thread(build_form)
            if rows:
                updates = _apply(rows)
                form_by_ticker.clear()
                form_by_ticker.update(updates)
                db.write_form(rows)
                if on_update:
                    try:
                        await on_update()
                    except Exception as e:
                        print(f"[form] on_update hook error: {e}", flush=True)
                print(f"[form] {len(rows)} players — matched {len(updates)}/{len(feed.markets)} markets", flush=True)
        except Exception as e:
            print(f"[form] refresh error: {e}", flush=True)
        await asyncio.sleep(REFRESH_SECONDS)


# ── standalone ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    rows = build_form()
    ranked = sorted([r for r in rows if r.get("form_score") is not None],
                    key=lambda r: -r["form_score"])
    print(f"{len(rows)} players fetched\n")
    print(f"{'player':22}{'form':>6}{'L5':>6}{'cut%':>6}{'scAvg':>7}{'GIR%':>6}{'drive':>7}{'p/gir':>7}")
    for r in ranked[:20]:
        sk = r.get("skills", {})
        v = lambda key: (sk.get(key) or {}).get("value")
        print(f"{r['name'][:22]:22}{str(r.get('form_score') or '-'):>6}"
              f"{str(r.get('avg_l5') or '-'):>6}"
              f"{str(r.get('made_cut_pct') or '-'):>6}"
              f"{str(r.get('scoring_avg') or '-'):>7}"
              f"{str(r.get('gir_pct') or '-'):>6}"
              f"{str(v('drive_dist') or '-'):>7}"
              f"{str(v('putts_gir') or '-'):>7}")
