import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

import db
import feed
import leaderboard


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    asyncio.create_task(feed.refresh_markets_loop())
    asyncio.create_task(feed.run_feed())
    asyncio.create_task(leaderboard.refresh_leaderboard_loop())
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
        })
    result.sort(key=lambda x: x["yes_bid"], reverse=True)
    return JSONResponse(result)


@app.get("/api/history/{ticker}")
async def get_history(ticker: str):
    return JSONResponse(list(feed.ticks.get(ticker, [])))


@app.get("/api/leaderboard")
async def get_leaderboard():
    return JSONResponse(leaderboard.board)


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
            "pos":         m.get("pos", "—"),
            "score":       m.get("score", "—"),
            "thru":        m.get("thru", "—"),
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
