import asyncio
import base64
import json
import re
import ssl
import time
from collections import defaultdict, deque

import certifi
import requests
import websockets
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

import config
import db

WS_URL   = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
REST_URL = "https://external-api.kalshi.com/trade-api/v2"
EVENT_TICKER = "KXPGATOUR-THMTPBW26"

TICK_MAXLEN = 300  # ~5 min at 1 tick/sec per market

# ── shared state (read by server.py) ──────────────────────────────────────────
markets: dict[str, dict] = {}
ticks:   defaultdict[str, deque] = defaultdict(lambda: deque(maxlen=TICK_MAXLEN))
trade_counts: defaultdict[str, int] = defaultdict(int)
broadcast_clients: set = set()


# ── helpers ───────────────────────────────────────────────────────────────────

def _player_name(title: str) -> str:
    m = re.match(r"Will (.+?) win", title)
    return m.group(1) if m else title


def _fetch_markets() -> list[dict]:
    resp = requests.get(
        f"{REST_URL}/markets",
        params={"event_ticker": EVENT_TICKER, "status": "open", "limit": 200},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["markets"]


def _auth_headers(private_key) -> dict:
    ts  = str(int(time.time() * 1000))
    sig = private_key.sign(
        (ts + "GET" + "/trade-api/ws/v2").encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return {
        "KALSHI-ACCESS-KEY":       config.API_KEY,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
        "KALSHI-ACCESS-TIMESTAMP": ts,
    }


async def _broadcast(msg: dict):
    if not broadcast_clients:
        return
    payload = json.dumps(msg)
    dead = set()
    for ws in broadcast_clients:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    broadcast_clients.difference_update(dead)


# ── market refresh (every 10 min) ─────────────────────────────────────────────

async def refresh_markets_loop():
    while True:
        try:
            for m in _fetch_markets():
                if m["ticker"] not in markets:
                    # Kalshi REST returns yes_bid_dollars / yes_ask_dollars (0-1);
                    # the plain yes_bid / yes_ask come back null. Prefer the
                    # _dollars fields, fall back to the plain names just in case.
                    markets[m["ticker"]] = {
                        "ticker":       m["ticker"],
                        "title":        m["title"],
                        "player":       _player_name(m["title"]),
                        "event_ticker": m["event_ticker"],
                        "yes_bid":      float(m.get("yes_bid_dollars") or m.get("yes_bid") or 0),
                        "yes_ask":      float(m.get("yes_ask_dollars") or m.get("yes_ask") or 0),
                        "volume_fp":    float(m.get("volume_fp") or 0),
                    }
            db.upsert_markets(list(markets.values()))
            print(f"[feed] {len(markets)} markets loaded")
        except Exception as e:
            print(f"[feed] market refresh error: {e}")
        await asyncio.sleep(600)


# ── event handlers ────────────────────────────────────────────────────────────

async def _on_ticker(msg: dict):
    ticker  = msg.get("market_ticker")
    if not ticker or ticker not in markets:
        return
    yes_bid = msg.get("yes_bid_dollars", msg.get("yes_bid", 0)) or 0
    yes_ask = msg.get("yes_ask_dollars", msg.get("yes_ask", 0)) or 0
    ts      = time.time()

    markets[ticker]["yes_bid"] = yes_bid
    markets[ticker]["yes_ask"] = yes_ask
    ticks[ticker].append({"ts": ts, "bid": yes_bid, "ask": yes_ask})
    db.write_tick(ticker, yes_bid, yes_ask)

    await _broadcast({"type": "ticker", "ticker": ticker,
                      "yes_bid": yes_bid, "yes_ask": yes_ask, "ts": ts})


async def _on_trade(msg: dict):
    ticker = msg.get("market_ticker")
    if not ticker:
        return
    side  = msg.get("taker_side", msg.get("side", "?"))
    price = msg.get("yes_price_dollars", msg.get("yes_price", msg.get("price", 0))) or 0
    count = msg.get("count")
    ts    = time.time()

    trade_counts[ticker] += count if count else 1
    db.write_trade(ticker, side, price, count)

    await _broadcast({"type": "trade", "ticker": ticker,
                      "side": side, "price": price, "count": count, "ts": ts})


# ── main feed loop ────────────────────────────────────────────────────────────

async def run_feed():
    # Wait until markets are loaded
    while not markets:
        await asyncio.sleep(0.5)

    private_key = config.load_private_key()
    ssl_ctx     = ssl.create_default_context(cafile=certifi.where())
    backoff     = 1

    while True:
        try:
            headers = _auth_headers(private_key)
            async with websockets.connect(WS_URL, additional_headers=headers, ssl=ssl_ctx) as ws:
                print(f"[feed] connected — subscribing to {len(markets)} markets")
                backoff = 1

                await ws.send(json.dumps({
                    "id": 1,
                    "cmd": "subscribe",
                    "params": {
                        "channels":       ["ticker", "trade"],
                        "market_tickers": list(markets.keys()),
                    },
                }))

                async for raw in ws:
                    envelope = json.loads(raw)
                    t = envelope.get("type")
                    if t == "ticker":
                        await _on_ticker(envelope["msg"])
                    elif t == "trade":
                        await _on_trade(envelope["msg"])
                    elif t == "error":
                        err  = envelope.get("msg", {})
                        code = err.get("code")
                        print(f"[feed] WS error {code}: {err.get('msg')}")
                        if code == 25:  # buffer overflow — reconnect
                            break

        except (websockets.ConnectionClosed, OSError) as e:
            print(f"[feed] disconnected: {e}. Reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
