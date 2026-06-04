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
- **model.py** — DataGolf model win-probability feed (Phase 4, paid API). `refresh_model_loop` every 120s pulls in-play (falls back to pre-tournament) win probs, normalizes to 0–1 (a price: 0.185 → 18.5¢), matches to the field via the leaderboard fuzzy matcher, annotates `feed.markets["...model"]`, broadcasts `type:"model"`, stores in `model_preds`. **All DataGolf-specifics are isolated to `_fetch_raw()` + `_parse()`** — swap those two to change provider. Needs `DATAGOLF_API_KEY` in `.env`; no key = graceful no-op. Dashboard shows Model price + a freshness clock + a live Δ (Model − True) coloured green/red.
- **form.py** — player recent-form from ESPN + **OWGR world ranking**, Phase 3.6. **OWGR comes from the modern owgr.com JSON API** (`apiweb.owgr.com/api/owgr/rankings/getRankings?pageSize=1000&...` — `rankingsList[].rank` + `player.fullName`; top-1000 covers any field; no scraping, the old BeautifulSoup approach is obsolete). Matched to the field via the leaderboard fuzzy matcher (72/72). ESPN itself exposes NO world ranking. `refresh_form_loop` every 6h (form moves between events). The PGA scoreboard `competitor.id` IS the ESPN athlete id, so the live field maps for free; one GET per player to `/athletes/{id}/overview` yields last-5 finishes (L1/L3/L5), season splits (events/cuts/top10/wins/scoring avg), and `seasonRankings.categories` (driving dist/acc, GIR, putts/GIR, birdies/rnd, money rank — the free Strokes-Gained proxies; ESPN has NO real SG or OWGR free). `build_form()` computes field z-scores + a composite `form_score` (mean of avg-L5-z & scoring-avg-z, both inverted so high = good). Annotates `feed.markets["...form"]`, broadcasts `type:"form"`, stored in the `form` table. Reuses leaderboard's fuzzy matching. Excludes the in-progress event so L1/L3/L5 are completed starts only. Coverage on Memorial: 72/72 (scoring avg 70/72 — Rory + A.Fitzpatrick under a non-PGA split). Probe: `form_probe.py`.
- **db.py** — thin SQLite helpers. Writes are synchronous and called directly from async code (safe at PGA data rates). DB file: `kalshi_data.db`. Tables: `markets`, `ticks`, `trades`, `leaderboard`, `poly_markets`, `poly_ticks`.
- **server.py** — FastAPI app. Starts feed + leaderboard tasks in `lifespan`. Exposes `/`, `/api/markets`, `/api/history/{ticker}`, `/api/leaderboard`, `/ws`.
- **config.py** — loads `.env` (API key) and `kalshi_private_key.pem` (RSA private key for WS auth).
- **dashboard/index.html** — single-file frontend. Connects to `/ws`, maintains its own in-browser tick history, renders table with flash animations, sparklines, detail panel with Chart.js chart and trade feed.

### Polymarket pieces (Phase 3 — built + tested STANDALONE, NOT yet imported by server.py)
- **polymarket_feed.py** — real-time Polymarket prices for the same tournament. Mirrors feed.py: `refresh_poly_markets_loop` (Gamma REST every 10 min, discovery/seed only) + `run_poly_feed` (CLOB market-channel WebSocket, `wss://ws-subscriptions-clob.polymarket.com/ws/market`, **no auth**). Subscribes by YES token id, uses the `best_bid`/`best_ask` fields Polymarket sends directly (do NOT reconstruct the book — that produced phantom asks), captures `last_trade_price`, sends `"PING"` every 10s. **We intentionally use NO Polymarket auth/keys — market data is public and this keeps the project un-tradeable.**
  - **Polymarket displayed-price rule (authoritative, in `server._poly_record`):** `poly_price = (best_bid+best_ask)/2` when spread ≤ **$0.10**, else the **last traded price**. This is exactly how Polymarket's site shows a price (their `/midpoint` endpoint = the same mid; the site adds the wide-spread→last fallback). We compute it live from the WS so no polling. The naive midpoint alone is WRONG on one-sided/wide books (e.g. Coody: no bid + 16¢ ask → mid 8¢, but he last traded 0.1¢). Microprice was tried and rejected (size walls drag it to the thin side). Kalshi books are tight, so K price = simple midpoint.
- **arbitrage.py** — cross-exchange arb math. Pure `best_arb()` (arb when one venue's BID > the other's ASK, net of fees), `--test` self-tests, `--live` scan. Full assumptions list in its module docstring; assumption #1 (resolution-rule equivalence) is the gate before trusting any edge.
- **polymarket_probe.py** — one-shot research/coverage script (Kalshi↔Polymarket price + name-match report).
- Matching across exchanges reuses `leaderboard.py`'s `normalize_name` + fuzzy fallback (both venues phrase markets as "Will X win …").

## Credentials
- `.env` — `KALSHI_API_KEY`, `KALSHI_PRIVATE_KEY_PATH`, and (optional) `DATAGOLF_API_KEY`
- `kalshi_private_key.pem` — RSA private key
- Both are gitignored. Never commit them.
- **DataGolf model key:** add `DATAGOLF_API_KEY=...` to `.env` (loaded in `config.py`). Optional — without it `model.py` no-ops and the Model/Δ columns show "—"; nothing else breaks.

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
- Kalshi uses the `_dollars` suffix on **both** the WS ticker AND the REST `/markets` response: `yes_bid_dollars` / `yes_ask_dollars` (and `volume_fp` for volume, `last_price_dollars` for the last trade). The plain `yes_bid` / `yes_ask` come back **null** from REST — do not rely on them. All are 0–1 floats; cast to `float()` when storing (values can be strings). *(This corrected an earlier wrong note that claimed REST used the plain names; `feed.refresh_markets_loop` was seeding bid/ask = 0 until the first WS tick.)*
- **The WS ticker/trade values arrive as STRINGS** (`'0.0340'`), not numbers — `feed._on_ticker`/`_on_trade` must `float()` them. They look fine until arithmetic: in JS `'0.034' + '0.039'` concatenates, so `(bid+ask)/2` = NaN. Symptom = "price correct on page load (the snapshot casts), goes null/NaN the instant the first live tick arrives." Cast at the source so the broadcast, DB write, and tick deque all get floats.
- **K Price** (and the sparkline/chart price series) mirrors Kalshi's site: two-sided book → midpoint; one-sided (an ask but no bid — ~30 of 72 longshots) → `last_price_dollars`; else the single side. A plain midpoint halves a lone ask into meaningless sub-cent noise — never use the midpoint alone.
- The `broadcast_clients` set is mutated during iteration in `_broadcast`. Dead clients are collected into a `dead` set and removed after iteration — do not change this pattern.
- `feed.markets` is a plain dict (not thread-safe), but this is fine since everything runs in a single asyncio event loop.
- Do not run `renderTable()` on every incoming WS tick — it re-renders the full DOM. Use targeted cell updates (`updateRow`) for live ticks and only call `renderTable()` on filter/sort changes.

## Archive
`archive/` contains original standalone scripts kept for reference (`market_data.py`, `realtime_feed.py`, `market_data_poly.py` — note the last is early Kalshi exploration despite the name, not Polymarket).
