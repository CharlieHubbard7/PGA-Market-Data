import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

import db
import feed
import form
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


_POLY_KEYS = ("poly_bid", "poly_ask", "poly_volume", "poly_last", "poly_mid", "poly_price")
_EMPTY_POLY = {k: None for k in _POLY_KEYS}


def _poly_record(pm: dict) -> dict:
    """Serialize a poly market dict into the wire fields the dashboard expects.

    poly_price replicates EXACTLY how Polymarket's site displays a price:
      • midpoint (best_bid + best_ask) / 2  when the spread is <= $0.10
      • else the last traded price          (a wide/one-sided book makes the mid
                                              meaningless — e.g. Coody: no bid + a
                                              16c ask gives mid 8c, but it last
                                              traded at 0.1c).
    We compute it from the real-time WS best bid/ask (same value as the CLOB
    /midpoint endpoint, but live), so no extra polling. Sensible fallbacks when
    there is no last trade."""
    bid, ask, last = pm.get("yes_bid"), pm.get("yes_ask"), pm.get("yes_last")
    POLY_WIDE_SPREAD = 0.10
    if bid is not None and ask is not None and (ask - bid) <= POLY_WIDE_SPREAD:
        price = (bid + ask) / 2
    elif last is not None:
        price = last
    elif bid is not None:
        price = bid
    elif ask is not None:
        price = ask
    else:
        price = None
    return {
        "poly_bid": bid, "poly_ask": ask,
        "poly_volume": pm.get("volume"), "poly_last": last,
        "poly_mid": pm.get("yes_mid"), "poly_price": price,
    }


def _poly_fields(ticker: str) -> dict:
    """Current Polymarket fields for a Kalshi ticker, or all-None."""
    _ensure_poly_map()
    for cid, tk in _cid_to_ticker.items():
        if tk == ticker:
            pm = polymarket_feed.poly_markets.get(cid)
            if pm:
                return _poly_record(pm)
            break
    return dict(_EMPTY_POLY)


async def _broadcast_poly(cid):
    _ensure_poly_map()
    tk = _cid_to_ticker.get(cid)
    pm = polymarket_feed.poly_markets.get(cid)
    if not tk or not pm:
        return
    await feed._broadcast({"type": "poly", "ticker": tk, **_poly_record(pm)})


async def _broadcast_form():
    """Push the whole per-ticker form map after each (slow) ESPN refresh."""
    await feed._broadcast({"type": "form", "markets": form.form_by_ticker})


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    asyncio.create_task(feed.refresh_markets_loop())
    asyncio.create_task(feed.run_feed())
    asyncio.create_task(leaderboard.refresh_leaderboard_loop())
    form.on_update = _broadcast_form
    asyncio.create_task(form.refresh_form_loop())
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


@app.get("/v2", response_class=HTMLResponse)
async def dashboard_v2():
    """Token-optimization variant: Advanced Stats collapsed by default."""
    return Path("dashboard/index_v2.html").read_text()


@app.get("/api/markets")
async def get_markets():
    result = []
    for ticker, m in feed.markets.items():
        bid = float(m["yes_bid"] or 0)
        ask = float(m["yes_ask"] or 0)
        mid = (bid + ask) / 2 if ask else bid
        result.append({
            "ticker":       ticker,
            "title":        m["title"],
            "player":       m["player"],
            "event_ticker": m["event_ticker"],
            "yes_bid":      bid,
            "yes_ask":      ask,
            "probability":  round(mid * 100, 1),
            "volume":       feed.trade_counts[ticker],
            "yes_last":     m.get("yes_last", 0),
            "pos":          m.get("pos", "—"),
            "score":        m.get("score", "—"),
            "thru":         m.get("thru", "—"),
            "round":        m.get("round", "—"),
            "form":         m.get("form"),
            **_poly_fields(ticker),
        })
    result.sort(key=lambda x: x["yes_bid"], reverse=True)
    return JSONResponse(result)


@app.get("/api/history/{ticker}")
async def get_history(ticker: str):
    return JSONResponse(list(feed.ticks.get(ticker, [])))


@app.get("/api/poly-history/{ticker}")
async def get_poly_history(ticker: str):
    """Polymarket bid/ask history for a Kalshi ticker, from in-memory poly_ticks.
    Lets the detail chart overlay the P price line on top of the K line."""
    _ensure_poly_map()
    cid = next((c for c, tk in _cid_to_ticker.items() if tk == ticker), None)
    if not cid:
        return JSONResponse([])
    return JSONResponse(list(polymarket_feed.poly_ticks.get(cid, [])))


@app.get("/api/leaderboard")
async def get_leaderboard():
    return JSONResponse(leaderboard.board)


@app.get("/api/form")
async def get_form():
    """Recent-form metrics keyed by Kalshi ticker."""
    return JSONResponse(form.form_by_ticker)


@app.get("/api/poly")
async def get_poly():
    """Polymarket bid/ask/volume keyed by the matched Kalshi ticker."""
    _ensure_poly_map()
    out = {}
    for cid, tk in _cid_to_ticker.items():
        pm = polymarket_feed.poly_markets.get(cid)
        if pm:
            out[tk] = _poly_record(pm)
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
        snapshot.append({
            "ticker":      ticker,
            "title":       m["title"],
            "player":      m["player"],
            "yes_bid":     bid,
            "yes_ask":     ask,
            "probability": round(mid * 100, 1),
            "volume":      feed.trade_counts[ticker],
            "yes_last":    m.get("yes_last", 0),
            "pos":         m.get("pos", "—"),
            "score":       m.get("score", "—"),
            "thru":        m.get("thru", "—"),
            "round":       m.get("round", "—"),
            "form":        m.get("form"),
            **_poly_fields(ticker),
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
