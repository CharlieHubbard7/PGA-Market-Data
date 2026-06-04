# TODO

Work through these sequentially. Each phase builds on the last.

---

## 📍 Current Status (2026-06-04)

**Live in the running app (`server.py`):** Kalshi WS feed + ESPN leaderboard → dashboard. Working, verified.

**Built + tested standalone, NOT yet wired into the app:**
- `polymarket_feed.py` — real-time Polymarket prices via CLOB market-channel WebSocket (no auth; read-only). Discovery via Gamma REST.
- `arbitrage.py` — cross-exchange arb math, 10/10 self-tests pass. Assumptions documented in its module docstring.
- `polymarket_probe.py` — research/coverage script.

**Decided:** 71/72 player mapping proven (reuses `leaderboard.py` matching). Polymarket needs no auth/keys for market data — and we deliberately don't use any (keeps the project un-tradeable). Real-time WS ≈ Kalshi (event-driven, sub-second); no meaningful lag.

**The gate before integration:** the arb premise rests on *assumption #1 — Kalshi and Polymarket resolving "win" identically* (playoffs, ties, WDs). Verify that before showing arb numbers as real. See `arbitrage.py` docstring for the full assumptions list.

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

## Known Bugs (small, fix anytime)

- [ ] **Numeric column sort direction is inverted / arrows lie** — In `visibleMarkets()` the string branch uses `sortDir * va.localeCompare(vb)` (sortDir=1 → ascending) but the numeric branch uses `sortDir * (vb - va)` (sortDir=1 → descending). Opposite polarity, so Bid/Ask/Volume/Pos/Score sort the *wrong way* relative to the sort arrow, and clicking feels inconsistent vs the Player column. **One-line fix:** change `return sortDir * (vb - va);` to `return sortDir * (va - vb);` — then sortDir=-1 (↓) = descending for both numbers and strings, and the arrows tell the truth. (Noticed by Charlie on Bid/Ask headers.)

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

- [x] **Research Polymarket API** — DONE via `polymarket_probe.py` (standalone, no app integration). Findings:
  - **Gamma API** (`https://gamma-api.polymarket.com`) gives everything we need for prices without the CLOB: `GET /events?slug=<slug>` returns the event with nested `markets[]`, each carrying `question`, `bestBid`, `bestAsk`, `outcomePrices` (JSON-string `["yes","no"]`), `volume`, and `clobTokenIds` (for later WS).
  - Memorial event slug: **`2026-the-memorial-tournament-presented-by-workday-winner`** (93 player markets, ~$75k volume). Same "Will X win" question format as Kalshi → identical name extraction.
  - `public-search?q=<term>` is the discovery endpoint for finding event slugs.
  - **Coverage: 71/72 real Kalshi players matched** (reusing `leaderboard.py` normalize + fuzzy). The 22 "unmatched" Polymarket entries are placeholders ("Player 0-19", "Any Other Player") + Jake Knapp (Polymarket-only). Kalshi-only: Sudarshan Yellamaraju.
  - ✅ **Bug found & FIXED:** Kalshi REST `/markets` returns `yes_bid_dollars`/`yes_ask_dollars`/`volume_fp` — NOT plain `yes_bid`/`yes_ask` (those are null). `feed.refresh_markets_loop` was seeding bid/ask = 0 until the first WS tick; now reads the `_dollars` fields. CLAUDE.md note corrected.
  - Note for the detector: real arb compares one book's ASK vs the other's BID (with fees), NOT mid-vs-mid. Polymarket spreads are very wide on thin markets, so mids are misleading.

- [x] **Build polymarket_feed.py** — DONE, standalone (NOT yet imported by server.py). **Now real-time via WebSocket** (upgraded from the 30s polling stopgap). Mirrors feed.py's two-task design:
  - `refresh_poly_markets_loop()` — Gamma REST every 10 min for DISCOVERY only (token ids, names, seed prices).
  - `run_poly_feed()` — CLOB market-channel WS (`wss://ws-subscriptions-clob.polymarket.com/ws/market`, NO auth). Subscribes to all 72 YES token ids with `custom_feature_enabled:true`; handles `book` / `price_change` / `best_bid_ask`; must send `"PING"` every 10s (server drops the socket otherwise); reconnect w/ backoff like feed.py.
  - Writes a `poly_ticks` row only when best bid/ask actually moves (dedupe — `price_change` fires on every order/cancel). Filters scaffolding markets → 72 real players.
  - Verified: WS connects, subscribes to 72 tokens, wrote 182 ticks across 60 markets in ~18s (vs 1 poll/30s before). Snapshot mode (`python3 polymarket_feed.py`) still works so arbitrage.py is unaffected.
  - 30s polling was MY choice, never a Polymarket limit (their REST limits are generous: Gamma /events 500/10s, CLOB /book 1500/10s). **Next: wire both tasks into server.py lifespan — only after arb assumptions (#1 resolution rules, #4 staleness) are addressed.**

- [x] **Map Kalshi ↔ Polymarket markets** — DONE by reusing `leaderboard.py` normalize + fuzzy matching (no separate mapping table needed — both use the "Will X win" format). Coverage proven: 71/72 real players (probe), 67 quotable-on-both (arb scan). Remaining mismatches are Polymarket-only (Jake Knapp) / Kalshi-only (Sudarshan Yellamaraju) / scaffolding markets.

- [ ] **Combined columns layout (Charlie's design)** — Do NOT add separate standalone Polymarket columns. Instead, group the two exchanges side by side under each metric so they're directly comparable:
  - **Bid:** Kalshi bid and Polymarket bid together (labeled K / P)
  - **Ask:** Kalshi ask and Polymarket ask together (labeled K / P)
  - **Volume:** Kalshi volume and Polymarket volume together (labeled K / P)
  - Likely a two-line cell or `K: __ / P: __` sub-layout per column. The point is at-a-glance cross-exchange comparison on the same row.

- [x] **Arbitrage detector (math)** — DONE + TESTED in `arbitrage.py` (standalone). Pure `best_arb()` compares both directions (arb exists when one venue's BID > other's ASK), nets out fees, returns the better. 10/10 self-tests pass (`python3 arbitrage.py --test`) — caught a real float-rounding bug in the Kalshi fee. Live scan (`python3 arbitrage.py`) joins 67 matched markets and flags net-positive edges; on current data the best is sub-cent (no meaningful free money — expected for a liquid favorite market). **Full assumptions list now lives in the `arbitrage.py` module docstring.**

- [x] **🚧 GATE: Verify resolution-rule equivalence (assumption #1)** — ✅ PASSED (2026-06-04). Compared Kalshi `rules_primary`/`rules_secondary` vs Polymarket event/market `description`+`resolutionSource`. Both resolve YES iff the player is the official PGA Tour winner (playoff included), NO otherwise (incl. WD-before-tee-off / missed cut); source pgatour.com; ~June 7 expiry. Same instrument. Only divergence: co-champions with NO playoff (weather) — Polymarket alphabetical-by-last-name, Kalshi silent — effectively never. Cross-exchange comparison is apples-to-apples; arb math is meaningful. **Gate cleared → integration unblocked.**

- [ ] **Wire Polymarket into server.py** — Add `refresh_poly_markets_loop()` + `run_poly_feed()` to the lifespan, **fault-isolated** (a Polymarket WS failure must NOT take down the Kalshi feed — wrap the tasks / guard the loops). Expose poly bid/ask to the dashboard (init snapshot + a `type:"poly"` broadcast keyed by Kalshi ticker via the existing name match).

- [ ] **Combined K/P columns + Arb column in dashboard** — Implement Charlie's combined-column design (see above): Bid K/P, Ask K/P, Volume K/P side-by-side, plus an "Arb" indicator highlighting net-positive rows. Label clearly as informational until the resolution-rule gate passes.

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
