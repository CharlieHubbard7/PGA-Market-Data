# Kalshi PGA Live — Claude Context

## What this project is
A real-time prediction market dashboard focused on PGA Tour golf. It streams live bid/ask data from Kalshi's WebSocket API, persists every tick and trade to SQLite, and serves a browser-based dashboard with a sortable/filterable market table and per-market price charts. The long-term goal is to collect enough data to build a model that identifies mispriced markets.

## How to run
```bash
cd ~/Desktop/kalshi_bot
python3 server.py
# open http://localhost:8000
```

## Architecture
```
Kalshi WS  ──►  feed.py  ──►  db.py (SQLite: kalshi_data.db)
                  │
                  └──► broadcast_clients (set of FastAPI WebSockets)
                            │
                          server.py (FastAPI)
                            │
                          dashboard/index.html (browser)
```

- **feed.py** — owns the Kalshi WebSocket connection, all shared state (`markets`, `ticks`, `trade_counts`, `broadcast_clients`), and all DB writes. Two async tasks: `refresh_markets_loop` (REST poll every 10 min) and `run_feed` (WS listener).
- **leaderboard.py** — polls ESPN's public golf scoreboard every 60s (`refresh_leaderboard_loop`), parses position/score/thru, and joins each player to a Kalshi market. Matching (`match()`) is exact-normalized-name first, then a last-name + first-initial fuzzy fallback; `normalize_name` transliterates special letters (Højgaard → hojgaard). Annotates `feed.markets` in place with `pos`/`score`/`thru` and broadcasts a per-ticker map (`type: "leaderboard"`). The browser never re-matches names — it just applies what the server sends.
- **db.py** — thin SQLite helpers. Writes are synchronous and called directly from async code (safe at PGA data rates). DB file: `kalshi_data.db`. Tables: `markets`, `ticks`, `trades`, `leaderboard`.
- **server.py** — FastAPI app. Starts feed + leaderboard tasks in `lifespan`. Exposes `/`, `/api/markets`, `/api/history/{ticker}`, `/api/leaderboard`, `/ws`.
- **config.py** — loads `.env` (API key) and `kalshi_private_key.pem` (RSA private key for WS auth).
- **dashboard/index.html** — single-file frontend. Connects to `/ws`, maintains its own in-browser tick history, renders table with flash animations, sparklines, detail panel with Chart.js chart and trade feed.

## Credentials
- `.env` — `KALSHI_API_KEY` and `KALSHI_PRIVATE_KEY_PATH`
- `kalshi_private_key.pem` — RSA private key
- Both are gitignored. Never commit them.

## Current tournament target
`EVENT_TICKER = "KXPGATOUR-THMTPBW26"` (Memorial Tournament presented by Workday, 2026)
Update this constant in `feed.py` when moving to a new tournament.

## Key decisions made
- **SQLite synchronous writes** over aiosqlite — writes are fast enough at PGA data rates and simpler to reason about.
- **Server subscribes to everything** — `ticker` + `trade` for all markets from startup. UI filtering is display-only. This ensures complete training data even when the user is watching a subset.
- **5-minute in-memory tick window** — `deque(maxlen=300)` per market. Chart in the UI shows this window. Full history is in SQLite.
- **Reconnect re-subscribes** — on WS disconnect, `run_feed` loops and re-subscribes with the current market list.
- **Code 25 (buffer overflow)** is caught and triggers an immediate reconnect.

## Things to be careful about
- Kalshi WS ticker fields use `yes_bid_dollars` / `yes_ask_dollars` (dollar suffix). REST API uses `yes_bid` / `yes_ask`. Both are 0–1 floats. Always cast to `float()` when storing — the REST API can return strings.
- The `broadcast_clients` set is mutated during iteration in `_broadcast`. Dead clients are collected into a `dead` set and removed after iteration — do not change this pattern.
- `feed.markets` is a plain dict (not thread-safe), but this is fine since everything runs in a single asyncio event loop.
- Do not run `renderTable()` on every incoming WS tick — it re-renders the full DOM. Use targeted cell updates (`updateRow`) for live ticks and only call `renderTable()` on filter/sort changes.

## Archive
`archive/` contains the original standalone scripts (`market_data.py`, `realtime_feed.py`) kept for reference.
