"""
Phase 3.6 research spike — what recent-form data can we get for free from ESPN?

STANDALONE (not imported by server.py). Run:
    python3 form_probe.py            # coverage report across the live field
    python3 form_probe.py --json     # dump the parsed form rows as JSON

What it does:
  1. Pull the PGA scoreboard → the current field (our ~72 players) WITH ESPN
     athlete ids (the scoreboard `competitor.id` IS the athlete id — confirmed:
     10166 → J.J. Spaun).
  2. For each athlete, fetch the `overview` endpoint (one call per player):
        https://site.web.api.espn.com/apis/common/v3/sports/golf/pga/athletes/{id}/overview
     which carries recentTournaments→eventsStats (last ~5 starts w/ finish
     position), statistics.splits (season EVENTS/CUTS/TOP10/WINS/AVG/EARNINGS),
     and seasonRankings.categories (driving dist/acc, GIR, putts/GIR, birdies/rnd
     — each with a PGA-Tour rank). No auth, no key.
  3. Parse the strongest decision-relevant data points, compute field-wide
     z-scores (so a number means "vs this week's field"), and print a coverage
     report: how many players we got, which fields are populated, sample rows.

ESPN does NOT expose Strokes Gained for free (that's DataGolf, paid). The SG
proxies here (driving, GIR, putting, scoring avg, recent finishes) are the best
free substitute; revisit DataGolf only if these prove too thin.
"""

import argparse
import concurrent.futures as cf
import json
import re
import statistics

import requests

from leaderboard import normalize_name

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard"
OVERVIEW   = "https://site.web.api.espn.com/apis/common/v3/sports/golf/pga/athletes/{id}/overview"
TIMEOUT    = 15

# seasonRankings categories worth keeping (the free SG proxies) → short label.
RANK_CATS = {
    "yardsPerDrive":   "drive_dist",
    "driveAccuracyPct":"drive_acc",
    "greensHit":       "greens_hit",
    "puttsGirAvg":     "putts_gir",
    "birdiesPerRound": "birdies_rnd",
}


# ── discovery: the current field + athlete ids ──────────────────────────────────

def fetch_field() -> list[dict]:
    """[{athlete_id, name, norm}] for every player in the live tournament field."""
    d = requests.get(SCOREBOARD, timeout=TIMEOUT).json()
    comps = d["events"][0]["competitions"][0]["competitors"]
    out = []
    for c in comps:
        aid = c.get("id")
        name = c.get("athlete", {}).get("displayName", "")
        if aid and name:
            out.append({"athlete_id": aid, "name": name, "norm": normalize_name(name)})
    return out


# ── parsing helpers ─────────────────────────────────────────────────────────────

def _finish_num(disp: str | None):
    """'T2'→2, '15'→15, 'CUT'/'WD'/'MC'→None (didn't make the weekend / N/A)."""
    if not disp:
        return None
    s = disp.strip().upper().lstrip("T")
    return int(s) if s.isdigit() else None


def _recent_finishes(overview: dict) -> list[dict]:
    """Last starts, newest first: [{event, date, pos, pos_num, completed}]."""
    rt = overview.get("recentTournaments") or []
    if not rt:
        return []
    out = []
    for e in rt[0].get("eventsStats", []):
        comps = e.get("competitions") or []
        if not comps:
            continue
        cps = comps[0].get("competitors") or []
        if not cps:
            continue
        st = cps[0].get("status", {})
        out.append({
            "event":     e.get("name", "?"),
            "date":      e.get("date", "")[:10],
            "pos":       (st.get("position") or {}).get("displayName"),
            "pos_num":   _finish_num((st.get("position") or {}).get("displayName")),
            "completed": (st.get("type") or {}).get("state") == "post",
        })
    return out


def _season_splits(overview: dict) -> dict:
    """PGA TOUR season totals: events, cuts, top10, wins, scoring_avg, earnings."""
    st = overview.get("statistics", {})
    labels = st.get("labels", [])
    for sp in st.get("splits", []):
        if sp.get("displayName") == "PGA TOUR":
            vals = dict(zip(labels, sp.get("stats", [])))
            def num(x):
                try: return float(str(x).replace(",", "").replace("$", ""))
                except (ValueError, TypeError): return None
            return {
                "events":      num(vals.get("EVENTS")),
                "cuts":        num(vals.get("CUTS")),
                "top10":       num(vals.get("TOP10")),
                "wins":        num(vals.get("WINS")),
                "scoring_avg": num(vals.get("AVG")),
            }
    return {}


def _rank_cats(overview: dict) -> dict:
    """SG-proxy season categories → {label: {value, rank}}."""
    cats = (overview.get("seasonRankings") or {}).get("categories", [])
    out = {}
    for c in cats:
        lbl = RANK_CATS.get(c.get("name"))
        if lbl:
            out[lbl] = {"value": c.get("value"), "rank": c.get("rank")}
    return out


def _earnings_rank(overview: dict):
    """PGA Tour money-won rank — ESPN's best free season-quality proxy (no OWGR)."""
    for c in (overview.get("seasonRankings") or {}).get("categories", []):
        if c.get("name") == "amount":
            return c.get("rank")
    return None


def fetch_form(player: dict) -> dict:
    """Pull + parse one player's overview. Never raises — returns ok=False on error."""
    row = {**player, "ok": False}
    try:
        ov = requests.get(OVERVIEW.format(id=player["athlete_id"]), timeout=TIMEOUT).json()
    except Exception as e:
        row["error"] = str(e)[:60]
        return row

    fins = _recent_finishes(ov)
    done = [f["pos_num"] for f in fins if f["completed"] and f["pos_num"] is not None]
    splits = _season_splits(ov)
    cats = _rank_cats(ov)

    row.update({
        "ok": True,
        "recent": fins,
        "n_completed_with_pos": len(done),
        "last1":  done[0] if len(done) >= 1 else None,
        "avg_l3": round(statistics.mean(done[:3]), 1) if len(done) >= 1 else None,
        "avg_l5": round(statistics.mean(done[:5]), 1) if len(done) >= 1 else None,
        **splits,
        "made_cut_pct": round(100 * splits["cuts"] / splits["events"], 0)
                        if splits.get("events") else None,
        "earnings_rank": _earnings_rank(ov),
        "cats": cats,
    })
    return row


# ── field-wide z-scores ─────────────────────────────────────────────────────────

def _zscore(rows: list[dict], field: str, invert: bool = False) -> None:
    """Annotate each row with f'{field}_z' (vs the field). invert=True when LOWER
    is better (scoring avg, finish position) so a high z always = good form."""
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    field = fetch_field()
    print(f"Field: {len(field)} players in the current tournament\n")

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        rows = list(ex.map(fetch_form, field))

    ok = [r for r in rows if r["ok"]]
    print(f"overview fetched OK: {len(ok)}/{len(rows)}")

    # field-wide z-scores: lower scoring avg / finish = better, so invert.
    _zscore(ok, "scoring_avg", invert=True)
    _zscore(ok, "avg_l5",      invert=True)

    if args.json:
        print(json.dumps(rows, indent=1))
        return

    # ── coverage report ──────────────────────────────────────────────────────
    def cov(field_name):
        n = sum(1 for r in ok if r.get(field_name) is not None)
        return f"{n}/{len(ok)}"
    print("\n=== field coverage (of players fetched OK) ===")
    for f in ["last1", "avg_l3", "avg_l5", "scoring_avg", "made_cut_pct",
              "top10", "wins", "events", "earnings_rank"]:
        print(f"  {f:14} {cov(f)}")
    print("  SG-proxy categories:")
    for lbl in RANK_CATS.values():
        n = sum(1 for r in ok if r.get("cats", {}).get(lbl))
        print(f"    {lbl:14} {n}/{len(ok)}")

    # ── sample table: best recent form (by avg_l5 z) ─────────────────────────
    ranked = sorted([r for r in ok if r.get("avg_l5") is not None],
                    key=lambda r: r["avg_l5"])
    print("\n=== top 18 by recent form (avg last-5 finish) ===")
    print(f"{'player':22}{'L1':>5}{'L3':>6}{'L5':>6}{'cut%':>6}{'scAvg':>7}{'top10':>6}{'formZ':>7}")
    for r in ranked[:18]:
        print(f"{r['name'][:22]:22}"
              f"{str(r.get('last1') or '-'):>5}"
              f"{str(r.get('avg_l3') or '-'):>6}"
              f"{str(r.get('avg_l5') or '-'):>6}"
              f"{str(int(r['made_cut_pct']) if r.get('made_cut_pct') is not None else '-'):>6}"
              f"{str(r.get('scoring_avg') or '-'):>7}"
              f"{str(int(r['top10']) if r.get('top10') is not None else '-'):>6}"
              f"{str(r.get('avg_l5_z') if r.get('avg_l5_z') is not None else '-'):>7}")

    thin = [r for r in ok if r.get("avg_l5") is None]
    if thin:
        print(f"\n{len(thin)} players have NO completed recent finish (rookies / "
              f"limited starts): {', '.join(r['name'] for r in thin[:12])}"
              f"{'...' if len(thin) > 12 else ''}")
    no_sc = [r for r in ok if r.get("scoring_avg") is None]
    if no_sc:
        print(f"{len(no_sc)} missing PGA-TOUR scoring avg (mostly intl/other-tour "
              f"splits): {', '.join(r['name'] for r in no_sc)}")
    failed = [r for r in rows if not r["ok"]]
    if failed:
        print(f"\n{len(failed)} overview fetch failures: "
              f"{', '.join(r['name'] for r in failed)}")


if __name__ == "__main__":
    main()
