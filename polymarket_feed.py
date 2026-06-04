"""
Polymarket feed — Phase 3, step 2.

Mirrors the shape of feed.py, but for Polymarket. Polymarket's Gamma API exposes
bestBid/bestAsk/volume directly, so this is a POLLING feed (no WebSocket needed
yet — the CLOB WS with clobTokenIds is a later optimization).

STANDALONE for now — NOT imported by server.py. Run it directly to test:

    python3 polymarket_feed.py          # one poll, prints a table
    python3 polymarket_feed.py --loop   # runs the async poll loop (Ctrl-C to stop)

Shared state (`poly_markets`, `poly_ticks`) and `refresh_poly_loop()` mirror
feed.py's names so wiring into server.py later is mechanical. Player names are
extracted with feed._player_name (same "Will X win" format as Kalshi), so the
existing leaderboard.match() logic will join the two exchanges downstream.
"""

import asyncio
import json
import re
import time
from collections import defaultdict, deque

import requests

import db
import feed   # read-only reuse: _player_name

GAMMA_URL       = "https://gamma-api.polymarket.com"
POLY_EVENT_SLUG = "2026-the-memorial-tournament-presented-by-workday-winner"
POLL_SECONDS    = 30
TICK_MAXLEN     = 300

# ── shared state (read by server.py later) ──────────────────────────────────────
# poly_markets: condition_id → market dict
# poly_ticks:   condition_id → deque of {ts, bid, ask, mid}
poly_markets: dict[str, dict] = {}
poly_ticks:   defaultdict[str, deque] = defaultdict(lambda: deque(maxlen=TICK_MAXLEN))

# Polymarket pads negRisk events with scaffolding markets named "Player 0".."Player N".
_PLACEHOLDER = re.compile(r"^player \d+$", re.I)


# ── helpers ───────────────────────────────────────────────────────────────────

def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _is_real_player(name: str) -> bool:
    return bool(name) and not _PLACEHOLDER.match(name) and name.lower() != "any other player"


def fetch_markets() -> list[dict]:
    """Pull the event from Gamma and return one row per real player market."""
    resp = requests.get(f"{GAMMA_URL}/events", params={"slug": POLY_EVENT_SLUG}, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    events = data if isinstance(data, list) else data.get("data", [])
    if not events:
        return []

    rows = []
    for m in events[0].get("markets", []):
        if m.get("closed"):
            continue
        player = feed._player_name(m.get("question", ""))   # "Will X win ..." -> "X"
        if not _is_real_player(player):
            continue
        # outcomePrices arrives as a JSON-encoded string: '["0.0095","0.9905"]'
        try:
            prices = json.loads(m.get("outcomePrices") or "[]")
        except (json.JSONDecodeError, TypeError):
            prices = []
        rows.append({
            "condition_id":   m.get("conditionId"),
            "question":       m.get("question", ""),
            "player":         player,
            "event_slug":     POLY_EVENT_SLUG,
            "yes_bid":        _f(m.get("bestBid")),                 # 0-1
            "yes_ask":        _f(m.get("bestAsk")),                 # 0-1
            "yes_mid":        _f(prices[0]) if prices else None,    # 0-1
            "volume":         _f(m.get("volume")) or 0.0,
            "clob_token_ids": m.get("clobTokenIds"),               # for future CLOB WS
        })
    return rows


# ── poll loop (every 30s) ────────────────────────────────────────────────────────

async def refresh_poly_loop():
    while True:
        try:
            rows = await asyncio.to_thread(fetch_markets)
            ts = time.time()
            for r in rows:
                cid = r["condition_id"]
                if not cid:
                    continue
                poly_markets[cid] = r
                poly_ticks[cid].append(
                    {"ts": ts, "bid": r["yes_bid"], "ask": r["yes_ask"], "mid": r["yes_mid"]}
                )
                db.write_poly_tick(cid, r["yes_bid"], r["yes_ask"], r["yes_mid"], r["volume"])
            if rows:
                db.upsert_poly_markets(rows)
                print(f"[poly] {len(rows)} markets updated", flush=True)
        except Exception as e:
            print(f"[poly] refresh error: {e}", flush=True)
        await asyncio.sleep(POLL_SECONDS)


# ── standalone test entrypoint ────────────────────────────────────────────────

def _print_snapshot():
    rows = fetch_markets()
    print(f"{len(rows)} real Polymarket player markets\n")
    print(f"{'Player':22} | {'bid':>6} {'ask':>6} {'mid':>6} | {'volume':>10}")
    print("-" * 60)
    for r in sorted(rows, key=lambda x: -(x["yes_mid"] or 0))[:15]:
        c = lambda v: f"{v*100:5.1f}¢" if v is not None else "   — "
        print(f"{r['player'][:22]:22} | {c(r['yes_bid'])} {c(r['yes_ask'])} {c(r['yes_mid'])} | {r['volume']:10,.0f}")


if __name__ == "__main__":
    import sys
    if "--loop" in sys.argv:
        db.init_db()
        try:
            asyncio.run(refresh_poly_loop())
        except KeyboardInterrupt:
            print("\nstopped")
    else:
        _print_snapshot()
