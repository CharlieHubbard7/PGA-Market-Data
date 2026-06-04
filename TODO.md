# TODO

Work through these sequentially. Each phase builds on the last.

---

## Phase 1 — Bug Fixes (do first) ✅ DONE

- [x] **Fix prob % filter** — `visibleMarkets()` now casts probability with `Number(m.probability) || 0`, so undefined/NaN values filter to 0 instead of silently passing. *(Superseded — the Prob % column and filter were later removed entirely; see Phase 2.5.)*

- [x] **Fix detail chart y-axis** — Added `chartjs-adapter-date-fns` and switched the chart x scale to `'time'` type, so sparse early ticks are placed by real time instead of clustering. (Also removed a dead `updateStats()` call that threw a ReferenceError on every init.)

---

## Phase 2 — ESPN Leaderboard Integration ✅ DONE

- [x] **Poll ESPN golf API** — `leaderboard.py` polls `https://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard` every 60s via `asyncio.to_thread`, parses competitors into rows (position with tie handling, total score, holes-thru derived from linescores), and snapshots each poll into the `leaderboard` SQLite table.

- [x] **Join leaderboard to market table** — Dashboard table has "Pos" and "Score" columns (score also shows holes-thru). Matching is done **server-side** in `leaderboard.match()`: exact normalized name first, then a last-name + first-initial fuzzy fallback. Normalization transliterates special letters (Højgaard → hojgaard). Verified 72/72 Memorial markets matched, including Nico↔Nicolas Echavarria and Matt↔Matthew McCarty.

- [x] **Leaderboard API endpoint** — `GET /api/leaderboard` returns the ordered board; the WS init snapshot and `type: "leaderboard"` broadcasts carry per-ticker `{pos, score, thru}` so the browser never re-matches names itself.

---

## Phase 2.5 — Dashboard Cleanup ✅ DONE

- [x] **Remove the Prob % column AND filter** — The implied-probability (mid-price) column was unreliable/confusing, so it was killed entirely: the "Prob %" table column, the Min/Max probability filter sliders, the detail-panel "Probability" readout, the `m.probability` computation, and the dead prob CSS are all gone. Default table sort moved to **Bid** (descending → favorites on top), which gives the same ordering the probability column used to. Search filter and all other columns unchanged.

- [x] **Faster ESPN polling** — `leaderboard.py` `POLL_SECONDS` lowered from 60 → 30. Tunable in one place if we want to go faster/slower. (Takes effect on server restart.)

---

## Phase 3 — Polymarket Integration (Arbitrage View)

> ⚠️ **Methodology for this phase — agreed with Charlie.** What we have now works and must not break.
> Build Polymarket support **incrementally, test-first, alongside the working Kalshi feed — never on top of it.**
> Concretely:
> 1. Each step gets validated in isolation (a throwaway script or a `tests/` probe) **before** it touches `server.py` or the dashboard.
> 2. Verify Polymarket data is correct (right markets, right prices, sane values) standalone before wiring it into the live app.
> 3. Prove the Kalshi↔Polymarket name/market mapping with a coverage report (like the 72/72 ESPN check) before trusting any arbitrage math.
> 4. Land it behind something we can toggle off, so a Polymarket outage can't take down the Kalshi dashboard.
> Work through it together, one verified step at a time.

- [ ] **Research Polymarket API** — Polymarket uses a CLOB (Central Limit Order Book). Find their WebSocket or polling endpoint for golf markets. Their API docs: https://docs.polymarket.com *(First deliverable: a standalone script that pulls and prints PGA market prices — no app integration yet.)*

- [ ] **Build polymarket_feed.py** — Mirror the structure of `feed.py`. Connect to Polymarket's feed, subscribe to PGA Tour markets, store prices in a `poly_ticks` SQLite table. *(Test standalone first: confirm prices stream and persist correctly before touching the server.)*

- [ ] **Map Kalshi ↔ Polymarket markets** — Build a mapping (JSON or DB table) pairing equivalent markets across exchanges. Hardest part — names and structures differ. **Reuse the proven matching approach from `leaderboard.py`** (normalize + fuzzy fallback) and produce a coverage report (X/Y markets matched) before relying on it.

- [ ] **Combined columns layout (Charlie's design)** — Do NOT add separate standalone Polymarket columns. Instead, group the two exchanges side by side under each metric so they're directly comparable:
  - **Bid:** Kalshi bid and Polymarket bid together (labeled K / P)
  - **Ask:** Kalshi ask and Polymarket ask together (labeled K / P)
  - **Volume:** Kalshi volume and Polymarket volume together (labeled K / P)
  - Likely a two-line cell or `K: __ / P: __` sub-layout per column. The point is at-a-glance cross-exchange comparison on the same row.

- [ ] **Arbitrage detector** — Add an `arbitrage.py` module that compares bid/ask across exchanges and flags when the spread implies a risk-free (or near risk-free) opportunity. Push alerts to the dashboard via WebSocket with `type: "arb_alert"`. *(Validate the math on recorded/sample data before flagging anything live.)*

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
