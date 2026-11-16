# Live PGA Tour prediction-market terminal

A real-time dashboard for "Will X win this tournament?" markets on **Kalshi** and **Polymarket**. It streams both order books over WebSocket and records every tick and trade to SQLite. Each market is joined to:
- the live ESPN leaderboard;
- each player's recent form and world ranking;
- a model win probability.

The goal is to see where market prices disagree with each other, or with a model, while a tournament is being played, and to build a tick-level dataset for finding mispriced markets.

```
Kalshi WS (RSA-signed)  ─┐
Polymarket CLOB WS      ─┼─► feed.py / polymarket_feed.py ─► SQLite (every tick + trade)
ESPN leaderboard (60s)  ─┤                │
ESPN form + OWGR (6h)   ─┤                ▼
DataGolf model (120s)   ─┘        FastAPI server.py ── /ws ──► dashboard (live table, charts)
```

## What it does

- **Streams both venues in real time.**
  - Kalshi uses an authenticated WebSocket (RSA-signed) and subscribes to ticker and trade channels for every market in the event.
  - Polymarket uses the public CLOB market channel, with no keys, so this project can't trade.
  - Reconnect, re-subscribe and buffer-overflow handling are built in.
- **Shows prices the way each exchange does.**
  - When a book is two-sided, the price is the midpoint.
  - When it's one-sided or the spread is wide, the price falls back to the last trade. Roughly 30 of 72 long-shot markets have an ask but no bid, and a naive midpoint turns those into sub-cent noise.
- **Matches names across sources.** Matching tries an exact normalized name first, then falls back to last name plus first initial, with transliteration (Højgaard → hojgaard). It mapped all 72 players in the live field.
- **Adds context columns:**
  - live position, score and holes played;
  - last 1, 3 and 5 finishes, season stats, and OWGR rank;
  - a composite form z-score across the field;
  - the DataGolf model's win probability, with a live model-vs-market Δ.
- **Checks for cross-exchange arbitrage** ([`arbitrage.py`](arbitrage.py)).
  - An arbitrage exists when one venue's bid exceeds the other's ask.
  - Kalshi's fee schedule is applied, `ceil(0.07·C·P·(1−P))`.
  - The module's docstring lists its assumptions; matching resolution rules on both venues is the gate before trusting any edge.
  - Self-tests: `python3 arbitrage.py --test`.

## Layout

| File | Role |
|---|---|
| `feed.py` | Kalshi REST discovery (10 min) and WebSocket stream; shared market state; DB writes |
| `polymarket_feed.py` | Polymarket Gamma discovery and CLOB WebSocket stream (standalone, not yet wired into the server) |
| `leaderboard.py` | ESPN leaderboard poll and cross-source name matching |
| `form.py` | ESPN recent form, OWGR rankings, field z-scores |
| `model.py` | DataGolf win probabilities; provider-specific code isolated in two functions |
| `arbitrage.py` | Cross-venue arbitrage math, live scan, self-tests |
| `db.py` | SQLite tables: `markets`, `ticks`, `trades`, `leaderboard`, `poly_markets`, `poly_ticks` |
| `server.py` | FastAPI: REST snapshot and history endpoints, `/ws` broadcast |
| `dashboard/index.html` | Single-file frontend: sortable live table, targeted cell updates, Chart.js price history |

## Run it

```bash
pip install fastapi uvicorn websockets requests python-dotenv cryptography certifi
# .env: KALSHI_API_KEY, KALSHI_PRIVATE_KEY_PATH, optional DATAGOLF_API_KEY
python3 server.py        # → http://localhost:8000
```

Set `EVENT_TICKER` in `feed.py` to the tournament you want. Without a DataGolf key, the model column stays empty and everything else runs.

## Notes

- All server state lives in one asyncio event loop; SQLite writes are synchronous, which is plenty fast at golf-market data rates.
- The server subscribes to every market regardless of what the UI is showing, so the recorded dataset is complete.
- [`CLAUDE.md`](CLAUDE.md) has the detailed engineering notes, including API quirks found along the way (Kalshi sends prices as strings; REST and WebSocket use different field names).
