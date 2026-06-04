"""
Standalone Polymarket probe — Phase 3, step 1.

Pulls the Memorial Tournament "Winner" markets from BOTH exchanges and prints a
coverage + price-comparison report. Does NOT touch server.py, the dashboard, or
the live feed — it only imports read-only helpers. Run it directly:

    python3 polymarket_probe.py

Goal of this probe (per the test-first plan in TODO.md Phase 3):
  1. Confirm we can fetch Polymarket PGA prices at all.
  2. Confirm the Kalshi <-> Polymarket player mapping (coverage report).
  3. Eyeball whether the two exchanges' prices are close or diverge (arb hint).

Nothing here is wired into the app yet. It's a throwaway validation script.
"""

import json

import requests

import feed                                   # read-only: _fetch_markets, _player_name
from leaderboard import normalize_name, _fuzzy_key

GAMMA_URL       = "https://gamma-api.polymarket.com"
# Polymarket event slug for the same tournament Kalshi streams (KXPGATOUR-THMTPBW26).
POLY_EVENT_SLUG = "2026-the-memorial-tournament-presented-by-workday-winner"


# ── fetch: Polymarket ───────────────────────────────────────────────────────────

def fetch_polymarket() -> tuple[dict, list[dict]]:
    """Return (event_meta, rows). Each row: player, bid, ask, yes, volume."""
    resp = requests.get(f"{GAMMA_URL}/events", params={"slug": POLY_EVENT_SLUG}, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    events = data if isinstance(data, list) else data.get("data", [])
    if not events:
        raise RuntimeError(f"No Polymarket event found for slug {POLY_EVENT_SLUG!r}")
    ev = events[0]

    rows = []
    for m in ev.get("markets", []):
        if m.get("closed"):
            continue
        player = feed._player_name(m.get("question", ""))   # "Will X win ..." -> "X"
        # outcomePrices arrives as a JSON-encoded string: '["0.0095","0.9905"]'
        try:
            prices = json.loads(m.get("outcomePrices") or "[]")
        except (json.JSONDecodeError, TypeError):
            prices = []
        rows.append({
            "player": player,
            "bid":    _f(m.get("bestBid")),          # YES best bid, 0-1
            "ask":    _f(m.get("bestAsk")),          # YES best ask, 0-1
            "yes":    _f(prices[0]) if prices else None,   # YES mid, 0-1
            "volume": _f(m.get("volume")) or 0.0,
        })
    return ev, rows


# ── fetch: Kalshi (reuse the live feed's helper) ─────────────────────────────────

def fetch_kalshi() -> list[dict]:
    # NOTE: Kalshi's REST /markets returns yes_bid_dollars / yes_ask_dollars /
    # volume_fp — NOT the plain yes_bid / yes_ask (those come back null here).
    # This differs from the assumption in CLAUDE.md; flagged for follow-up.
    rows = []
    for m in feed._fetch_markets():
        rows.append({
            "player": feed._player_name(m.get("title", "")),
            "bid":    _f(m.get("yes_bid_dollars", m.get("yes_bid"))),   # 0-1
            "ask":    _f(m.get("yes_ask_dollars", m.get("yes_ask"))),   # 0-1
            "volume": _f(m.get("volume_fp", m.get("volume"))) or 0.0,
        })
    return rows


# ── matching (same approach proven in leaderboard.py) ────────────────────────────

def build_index(rows: list[dict]) -> tuple[dict, dict]:
    by_name, by_key = {}, {}
    for r in rows:
        by_name[normalize_name(r["player"])] = r
        k = _fuzzy_key(r["player"])
        if k:
            by_key.setdefault(k, r)
    return by_name, by_key


def match(player: str, by_name: dict, by_key: dict) -> dict | None:
    r = by_name.get(normalize_name(player))
    if r:
        return r
    k = _fuzzy_key(player)
    return by_key.get(k) if k else None


# ── helpers ───────────────────────────────────────────────────────────────────

def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _c(v):
    """0-1 float -> cents string for display."""
    return f"{v*100:5.1f}¢" if v is not None else "   — "


# ── report ──────────────────────────────────────────────────────────────────────

def main():
    print("Fetching Polymarket ...")
    ev, poly = fetch_polymarket()
    print(f"  Polymarket event: {ev.get('title')}")
    print(f"  open player markets: {len(poly)} | event volume: ${_f(ev.get('volume')) or 0:,.0f}\n")

    print("Fetching Kalshi ...")
    kal = fetch_kalshi()
    print(f"  Kalshi open markets: {len(kal)}\n")

    k_by_name, k_by_key = build_index(kal)

    matched, unmatched = [], []
    for p in poly:
        m = match(p["player"], k_by_name, k_by_key)
        (matched if m else unmatched).append((p, m))

    print("=" * 78)
    print(f"COVERAGE: {len(matched)}/{len(poly)} Polymarket players matched to a Kalshi market")
    print("=" * 78)

    # Side-by-side, sorted by Polymarket YES price (favorites first).
    matched.sort(key=lambda pm: -(pm[0]["yes"] or 0))
    hdr = f"{'Player':22} | {'K bid':>6} {'K ask':>6} | {'P bid':>6} {'P ask':>6} | {'YES K/P':>13} | spread"
    print("\n" + hdr)
    print("-" * len(hdr))
    for p, k in matched:
        k_yes = (k["bid"] + k["ask"]) / 2 if (k["bid"] and k["ask"]) else k["bid"]
        diff  = (p["yes"] - k_yes) * 100 if (p["yes"] is not None and k_yes) else None
        diff_s = f"{diff:+5.1f}¢" if diff is not None else "  — "
        print(f"{p['player'][:22]:22} | {_c(k['bid'])} {_c(k['ask'])} | "
              f"{_c(p['bid'])} {_c(p['ask'])} | {_c(k_yes)}/{_c(p['yes'])} | {diff_s}")

    if unmatched:
        print(f"\nUNMATCHED Polymarket players ({len(unmatched)}) — no Kalshi market found:")
        for p, _ in unmatched:
            print(f"  - {p['player']}  (YES {_c(p['yes'])})")

    # Reverse check: Kalshi players with no Polymarket counterpart.
    p_by_name, p_by_key = build_index(poly)
    k_only = [k for k in kal if not match(k["player"], p_by_name, p_by_key)]
    if k_only:
        print(f"\nKalshi players with NO Polymarket market ({len(k_only)}):")
        for k in k_only:
            print(f"  - {k['player']}")


if __name__ == "__main__":
    main()
