import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

import db
import feed
import leaderboard
import polymarket_feed
from leaderboard import normalize_name, _fuzzy_key

# Toggle for the whole Polymarket integration. If a Polymarket outage ever
# becomes a problem, flip this to False and the Kalshi dashboard is untouched.
POLY_ENABLED = True


# ── Polymarket bridge ────────────────────────────────────────────────────────────
# Polymarket markets are keyed by condition_id; the dashboard is keyed by Kalshi
# ticker. We map between them by player name (same matcher as leaderboard.py) and
# rebuild only when the set of known poly markets changes.
_cid_to_ticker: dict[str, str] = {}
_poly_map_built_for = -1


def _rebuild_poly_map():
    k_by_name, k_by_key = {}, {}
    for tk, m in feed.markets.items():
        k_by_name[normalize_name(m["player"])] = tk
        key = _fuzzy_key(m["player"])
        if key:
            k_by_key.setdefault(key, tk)
    _cid_to_ticker.clear()
    for cid, pm in polymarket_feed.poly_markets.items():
        key = _fuzzy_key(pm["player"])
        tk = k_by_name.get(normalize_name(pm["player"])) or (k_by_key.get(key) if key else None)
        if tk:
            _cid_to_ticker[cid] = tk


def _ensure_poly_map():
    global _poly_map_built_for
    n = len(polymarket_feed.poly_markets)
    if feed.markets and n != _poly_map_built_for:
        _rebuild_poly_map()
        _poly_map_built_for = n


def _poly_fields(ticker: str):
    """Current Polymarket (bid, ask, volume) for a Kalshi ticker, or (None,)*3."""
    _ensure_poly_map()
    for cid, tk in _cid_to_ticker.items():
        if tk == ticker:
            pm = polymarket_feed.poly_markets.get(cid)
            if pm:
                return (pm.get("yes_bid"), pm.get("yes_ask"), pm.get("volume"))
            break
    return (None, None, None)


async def _broadcast_poly(cid, player, bid, ask, volume):
    _ensure_poly_map()
    tk = _cid_to_ticker.get(cid)
    if not tk:
        return
    await feed._broadcast({
        "type": "poly", "ticker": tk,
        "poly_bid": bid, "poly_ask": ask, "poly_volume": volume,
    })


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    asyncio.create_task(feed.refresh_markets_loop())
    asyncio.create_task(feed.run_feed())
    asyncio.create_task(leaderboard.refresh_leaderboard_loop())
    if POLY_ENABLED:
        # Fault-isolated: these are independent tasks, and the feed's own loops
        # swallow their exceptions, so a Polymarket failure can't stop Kalshi.
        polymarket_feed.on_update = _broadcast_poly
        asyncio.create_task(polymarket_feed.refresh_poly_markets_loop())
        asyncio.create_task(polymarket_feed.run_poly_feed())
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return Path("dashboard/index.html").read_text()


@app.get("/api/markets")
async def get_markets():
    result = []
    for ticker, m in feed.markets.items():
        bid = float(m["yes_bid"] or 0)
        ask = float(m["yes_ask"] or 0)
        mid = (bid + ask) / 2 if ask else bid
        p_bid, p_ask, p_vol = _poly_fields(ticker)
        result.append({
            "ticker":       ticker,
            "title":        m["title"],
            "player":       m["player"],
            "event_ticker": m["event_ticker"],
            "yes_bid":      bid,
            "yes_ask":      ask,
            "probability":  round(mid * 100, 1),
            "volume":       feed.trade_counts[ticker],
            "pos":          m.get("pos", "—"),
            "score":        m.get("score", "—"),
            "thru":         m.get("thru", "—"),
            "poly_bid":     p_bid,
            "poly_ask":     p_ask,
            "poly_volume":  p_vol,
        })
    result.sort(key=lambda x: x["yes_bid"], reverse=True)
    return JSONResponse(result)


@app.get("/api/history/{ticker}")
async def get_history(ticker: str):
    return JSONResponse(list(feed.ticks.get(ticker, [])))


@app.get("/api/leaderboard")
async def get_leaderboard():
    return JSONResponse(leaderboard.board)


@app.get("/api/poly")
async def get_poly():
    """Polymarket bid/ask/volume keyed by the matched Kalshi ticker."""
    _ensure_poly_map()
    out = {}
    for cid, tk in _cid_to_ticker.items():
        pm = polymarket_feed.poly_markets.get(cid)
        if pm:
            out[tk] = {"poly_bid": pm.get("yes_bid"), "poly_ask": pm.get("yes_ask"),
                       "poly_volume": pm.get("volume")}
    return JSONResponse(out)


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    feed.broadcast_clients.add(websocket)

    # Send full market snapshot on connect
    snapshot = []
    for ticker, m in feed.markets.items():
        bid = float(m["yes_bid"] or 0)
        ask = float(m["yes_ask"] or 0)
        mid = (bid + ask) / 2 if ask else bid
        p_bid, p_ask, p_vol = _poly_fields(ticker)
        snapshot.append({
            "ticker":      ticker,
            "title":       m["title"],
            "player":      m["player"],
            "yes_bid":     bid,
            "yes_ask":     ask,
            "probability": round(mid * 100, 1),
            "volume":      feed.trade_counts[ticker],
            "pos":         m.get("pos", "—"),
            "score":       m.get("score", "—"),
            "thru":        m.get("thru", "—"),
            "poly_bid":    p_bid,
            "poly_ask":    p_ask,
            "poly_volume": p_vol,
        })
    await websocket.send_text(json.dumps({
        "type": "init",
        "markets": snapshot,
        "leaderboard": leaderboard.board,
    }))

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        feed.broadcast_clients.discard(websocket)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
