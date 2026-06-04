"""
Polymarket feed — Phase 3, step 2 (real-time WebSocket version).

Mirrors feed.py's two-task design:
  - refresh_poly_markets_loop(): polls Gamma every 10 min to DISCOVER markets
    (token ids, player names, seed prices). Metadata only — like feed.refresh_markets_loop.
  - run_poly_feed(): connects to Polymarket's CLOB market-channel WebSocket and
    streams live best bid/ask in real time — like feed.run_feed.

Why WebSocket, not polling: Polymarket's market channel pushes book / price_change /
best_bid_ask events the instant the book moves (we measured ~10 msgs/sec on 6
markets). Polling every 30s was a stopgap, never a Polymarket limit. The CLOB
market channel needs NO auth; subscribe by token id (we have clobTokenIds), and
send "PING" every 10s or the server drops the connection after ~10s.

STANDALONE for now — NOT imported by server.py. Run directly:
    python3 polymarket_feed.py          # one Gamma snapshot, prints a table
    python3 polymarket_feed.py --loop   # live: Gamma discovery + WS stream (Ctrl-C)
"""

import asyncio
import json
import re
import ssl
import time
from collections import defaultdict, deque

import certifi
import requests
import websockets

import db
import feed   # read-only reuse: _player_name

GAMMA_URL       = "https://gamma-api.polymarket.com"
WS_URL          = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
POLY_EVENT_SLUG = "2026-the-memorial-tournament-presented-by-workday-winner"
REFRESH_SECONDS = 600    # Gamma metadata refresh (discover new markets), NOT price cadence
PING_SECONDS    = 10     # market channel drops the socket after ~10s without a PING
TICK_MAXLEN     = 300

# ── shared state (read by server.py later) ──────────────────────────────────────
# poly_markets:  condition_id → market dict (live yes_bid/yes_ask kept current by WS)
# poly_ticks:    condition_id → deque of {ts, bid, ask}
# _token_to_cid: YES token id → condition_id, for routing WS messages
poly_markets:  dict[str, dict] = {}
poly_ticks:    defaultdict[str, deque] = defaultdict(lambda: deque(maxlen=TICK_MAXLEN))
_token_to_cid: dict[str, str] = {}

# Optional async hook set by server.py: await on_update(condition_id, player, bid, ask, volume)
# whenever a market's best bid/ask actually moves. Left None when run standalone.
on_update = None

_PLACEHOLDER = re.compile(r"^player \d+$", re.I)


# ── helpers ───────────────────────────────────────────────────────────────────

def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _is_real_player(name: str) -> bool:
    return bool(name) and not _PLACEHOLDER.match(name) and name.lower() != "any other player"


def _yes_token(market: dict) -> str | None:
    """First clobTokenId is the YES token (matches outcomes order ["Yes","No"])."""
    ids = market.get("clob_token_ids")
    if isinstance(ids, str):
        try:
            ids = json.loads(ids)
        except json.JSONDecodeError:
            ids = None
    return ids[0] if ids else None


# ── discovery: Gamma REST (metadata + seed prices) ───────────────────────────────

def fetch_markets() -> list[dict]:
    """Pull the event from Gamma and return one row per real player market.
    Used for discovery/seeding here, and directly by arbitrage.py / the probe."""
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
        player = feed._player_name(m.get("question", ""))
        if not _is_real_player(player):
            continue
        try:
            prices = json.loads(m.get("outcomePrices") or "[]")
        except (json.JSONDecodeError, TypeError):
            prices = []
        rows.append({
            "condition_id":   m.get("conditionId"),
            "question":       m.get("question", ""),
            "player":         player,
            "event_slug":     POLY_EVENT_SLUG,
            "yes_bid":        _f(m.get("bestBid")),
            "yes_ask":        _f(m.get("bestAsk")),
            "yes_mid":        _f(prices[0]) if prices else None,   # Gamma's displayed price
            "yes_last":       _f(m.get("lastTradePrice")),         # last trade print
            "volume":         _f(m.get("volume")) or 0.0,
            "clob_token_ids": m.get("clobTokenIds"),
        })
    return rows


async def refresh_poly_markets_loop():
    """Discover markets + token ids every REFRESH_SECONDS. Seeds bid/ask on first
    sight but does NOT overwrite live WS values on subsequent refreshes."""
    while True:
        try:
            rows = await asyncio.to_thread(fetch_markets)
            for r in rows:
                cid = r["condition_id"]
                if not cid:
                    continue
                if cid in poly_markets:
                    poly_markets[cid]["volume"] = r["volume"]          # refresh metadata only
                else:
                    poly_markets[cid] = r                              # seed new market
                tok = _yes_token(r)
                if tok:
                    _token_to_cid[tok] = cid
            if rows:
                db.upsert_poly_markets(rows)
                print(f"[poly] {len(poly_markets)} markets known ({len(_token_to_cid)} tokens)", flush=True)
        except Exception as e:
            print(f"[poly] market refresh error: {e}", flush=True)
        await asyncio.sleep(REFRESH_SECONDS)


# ── live: CLOB market-channel WebSocket ──────────────────────────────────────────

async def run_poly_feed():
    while not _token_to_cid:                      # wait for discovery to populate tokens
        await asyncio.sleep(0.5)

    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    backoff = 1
    while True:
        try:
            async with websockets.connect(WS_URL, ssl=ssl_ctx) as ws:
                tokens = list(_token_to_cid.keys())
                await ws.send(json.dumps({
                    "type": "market",
                    "assets_ids": tokens,
                    "custom_feature_enabled": True,   # enables best_bid_ask events
                }))
                print(f"[poly] WS connected — subscribed to {len(tokens)} tokens", flush=True)
                backoff = 1
                pinger = asyncio.create_task(_ping_loop(ws))
                try:
                    async for raw in ws:
                        if raw == "PONG":
                            continue
                        data = json.loads(raw)
                        for msg in (data if isinstance(data, list) else [data]):
                            await _on_message(msg)
                finally:
                    pinger.cancel()
        except (websockets.ConnectionClosed, OSError) as e:
            print(f"[poly] WS disconnected: {e}. Reconnecting in {backoff}s...", flush=True)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


async def _ping_loop(ws):
    while True:
        await asyncio.sleep(PING_SECONDS)
        await ws.send("PING")


async def _on_message(msg: dict):
    et = msg.get("event_type")
    # Polymarket sends authoritative best_bid/best_ask in these messages — trust
    # them directly (no book reconstruction). Midpoint of these == Polymarket's
    # own published mark (outcomePrices) == the number their app shows.
    if et == "best_bid_ask":
        await _update(msg.get("asset_id"), msg.get("best_bid"), msg.get("best_ask"))
    elif et == "price_change":
        for pc in msg.get("price_changes", []):
            await _update(pc.get("asset_id"), pc.get("best_bid"), pc.get("best_ask"))
    elif et == "book":
        await _update(msg.get("asset_id"),
                      _best_bid(msg.get("bids")), _best_ask(msg.get("asks")))
    elif et == "last_trade_price":
        # Fires on every trade — drives freshness + a last-trade reference.
        cid = _token_to_cid.get(msg.get("asset_id"))
        price = _f(msg.get("price"))
        if cid and cid in poly_markets and price is not None:
            poly_markets[cid]["yes_last"] = price
            await _emit(cid)
    # ignored: new_market, tick_size_change


def _best_bid(levels):
    prices = [float(l["price"]) for l in (levels or []) if l.get("price") is not None]
    return max(prices) if prices else None


def _best_ask(levels):
    prices = [float(l["price"]) for l in (levels or []) if l.get("price") is not None]
    return min(prices) if prices else None


async def _update(token, bid, ask):
    """Update best bid/ask for a token's market. Writes a tick + emits ONLY when the
    best bid/ask actually moved (price_change fires on every order/cancel)."""
    cid = _token_to_cid.get(token)
    if not cid or cid not in poly_markets:
        return
    m = poly_markets[cid]
    new_bid = _f(bid) if bid is not None else m.get("yes_bid")
    new_ask = _f(ask) if ask is not None else m.get("yes_ask")
    if new_bid == m.get("yes_bid") and new_ask == m.get("yes_ask"):
        return
    m["yes_bid"], m["yes_ask"] = new_bid, new_ask
    mid = (new_bid + new_ask) / 2 if (new_bid is not None and new_ask is not None) else None
    poly_ticks[cid].append({"ts": time.time(), "bid": new_bid, "ask": new_ask})
    db.write_poly_tick(cid, new_bid, new_ask, mid, m.get("volume"))
    await _emit(cid)


async def _emit(cid):
    """Signal the server (if wired) that this market changed; it reads the full
    record from poly_markets[cid]. Guarded so a broadcast error can't kill the feed."""
    if on_update is None or cid not in poly_markets:
        return
    try:
        await on_update(cid)
    except Exception as e:
        print(f"[poly] on_update hook error: {e}", flush=True)


# ── standalone test entrypoint ────────────────────────────────────────────────

def _print_snapshot():
    rows = fetch_markets()
    print(f"{len(rows)} real Polymarket player markets\n")
    print(f"{'Player':22} | {'bid':>6} {'ask':>6} {'mid':>6} | {'volume':>10}")
    print("-" * 60)
    for r in sorted(rows, key=lambda x: -(x["yes_mid"] or 0))[:15]:
        c = lambda v: f"{v*100:5.1f}¢" if v is not None else "   — "
        print(f"{r['player'][:22]:22} | {c(r['yes_bid'])} {c(r['yes_ask'])} {c(r['yes_mid'])} | {r['volume']:10,.0f}")


async def _run_live():
    db.init_db()
    await asyncio.gather(refresh_poly_markets_loop(), run_poly_feed())


if __name__ == "__main__":
    import sys
    if "--loop" in sys.argv:
        try:
            asyncio.run(_run_live())
        except KeyboardInterrupt:
            print("\nstopped")
    else:
        _print_snapshot()
