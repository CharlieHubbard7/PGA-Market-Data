# TODO

Work through these sequentially. Each phase builds on the last.

---

## Phase 1 — Bug Fixes (do first) ✅ DONE

- [x] **Fix prob % filter** — `visibleMarkets()` now casts probability with `Number(m.probability) || 0`, so undefined/NaN values filter to 0 instead of silently passing.

- [x] **Fix detail chart y-axis** — Added `chartjs-adapter-date-fns` and switched the chart x scale to `'time'` type, so sparse early ticks are placed by real time instead of clustering. (Also removed a dead `updateStats()` call that threw a ReferenceError on every init.)

---

## Phase 2 — ESPN Leaderboard Integration ✅ DONE

- [x] **Poll ESPN golf API** — `leaderboard.py` polls `https://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard` every 60s via `asyncio.to_thread`, parses competitors into rows (position with tie handling, total score, holes-thru derived from linescores), and snapshots each poll into the `leaderboard` SQLite table.

- [x] **Join leaderboard to market table** — Dashboard table has "Pos" and "Score" columns (score also shows holes-thru). Matching is done **server-side** in `leaderboard.match()`: exact normalized name first, then a last-name + first-initial fuzzy fallback. Normalization transliterates special letters (Højgaard → hojgaard). Verified 72/72 Memorial markets matched, including Nico↔Nicolas Echavarria and Matt↔Matthew McCarty.

- [x] **Leaderboard API endpoint** — `GET /api/leaderboard` returns the ordered board; the WS init snapshot and `type: "leaderboard"` broadcasts carry per-ticker `{pos, score, thru}` so the browser never re-matches names itself.

---

## Phase 3 — Polymarket Integration (Arbitrage View)

- [ ] **Research Polymarket API** — Polymarket uses a CLOB (Central Limit Order Book). Find their WebSocket or polling endpoint for golf markets. Their API docs: https://docs.polymarket.com

- [ ] **Build polymarket_feed.py** — Mirror the structure of `feed.py`. Connect to Polymarket's feed, subscribe to PGA Tour markets, store prices in a `poly_ticks` SQLite table.

- [ ] **Map Kalshi ↔ Polymarket markets** — Build a mapping file (JSON or DB table) that pairs equivalent markets across exchanges. This is the hardest part — names and structures differ.

- [ ] **Arbitrage detector** — Add an `arbitrage.py` module that compares bid/ask across exchanges and flags when the spread implies a risk-free (or near risk-free) opportunity. Push alerts to the dashboard via WebSocket with `type: "arb_alert"`.

- [ ] **Arbitrage column in dashboard** — Add an "Arb" indicator column to the table. Highlight rows where a cross-exchange opportunity exists.

---

## Phase 4 — Model Foundation

- [ ] **Confirm `count` field** — Log a raw Kalshi trade message to verify the actual field names available. Update `handle_trade` in `feed.py` to capture all available fields. This affects the volume column accuracy.

- [ ] **Feature engineering script** — Write `features.py` that queries `kalshi_data.db` and builds a labeled dataset: for each market, at each point in time, what were the features (bid, ask, spread, volume rate, leaderboard position, holes remaining) and what was the outcome (did this player win?).

- [ ] **Baseline model** — Train a simple logistic regression or gradient boosted model on completed tournament data. Compare predicted probability to Kalshi's implied probability to identify systematic mispricings.

---

## Ongoing / Housekeeping

- [ ] Update `EVENT_TICKER` in `feed.py` at the start of each new tournament.
- [ ] Archive `kalshi_data.db` after each tournament with the event ticker as a suffix (e.g., `kalshi_data_THMTPBW26.db`).
- [ ] Consider adding a `/api/raw-trade-sample` endpoint that dumps the last 5 raw trade messages — useful for debugging field names on new tournaments.
