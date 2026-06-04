# TODO

Work through these sequentially. Each phase builds on the last.

---

## 📍 Current Status (2026-06-04)

**Live in the running app (`server.py`):** Kalshi WS feed + ESPN leaderboard + **Polymarket WS feed** → dashboard. The table now has **separate K/P columns** (K Bid, P Bid, K Ask, P Ask, K Vol, P Vol), a **Speed** column (per-venue freshness dot + updates/min), and a freshness-aware **Arb** column. All verified; Kalshi unaffected. Polymarket behind a `POLY_ENABLED` flag.

**Phase 3 (Polymarket + arb) is complete.** Current focus shifts to **data richness + presentation**, NOT a model:
- Phase 3.5 — Polymarket single-price column (highlighted) + capture last_trade_price + best-odds/line-shopping highlight + detail-chart fix + Kalshi volume-units fix.
- Phase 3.6 — NEW data source: player recent-form (last 1/3/5 starts, z-score) — research spike first.
- Phase 5 — predictive model: DEFERRED, way down the line (Charlie's call).

**Reference scripts (standalone):**
- `arbitrage.py` — arb math, 10/10 self-tests; the dashboard has a JS port that must stay in sync. Assumptions in its docstring (#1 resolution rules ✅ verified).
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

## Known Bugs

- [x] **Numeric column sort direction was inverted / arrows lied** — FIXED. Changed the numeric branch in `visibleMarkets()` from `sortDir * (vb - va)` to `sortDir * (va - vb)`, matching the string branch. Now sortDir=-1 (↓) = descending for both numbers and strings, and the arrows tell the truth. (Fixed while adding the new sortable K/P/Arb columns.)

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

- [x] ~~**Combined columns layout**~~ — Superseded. Started stacked, then Charlie chose **separate** sortable K/P columns (done — see "K/P columns in dashboard" below).

- [x] **Arbitrage detector (math)** — DONE + TESTED in `arbitrage.py` (standalone). Pure `best_arb()` compares both directions (arb exists when one venue's BID > other's ASK), nets out fees, returns the better. 10/10 self-tests pass (`python3 arbitrage.py --test`) — caught a real float-rounding bug in the Kalshi fee. Live scan (`python3 arbitrage.py`) joins 67 matched markets and flags net-positive edges; on current data the best is sub-cent (no meaningful free money — expected for a liquid favorite market). **Full assumptions list now lives in the `arbitrage.py` module docstring.**

- [x] **🚧 GATE: Verify resolution-rule equivalence (assumption #1)** — ✅ PASSED (2026-06-04). Compared Kalshi `rules_primary`/`rules_secondary` vs Polymarket event/market `description`+`resolutionSource`. Both resolve YES iff the player is the official PGA Tour winner (playoff included), NO otherwise (incl. WD-before-tee-off / missed cut); source pgatour.com; ~June 7 expiry. Same instrument. Only divergence: co-champions with NO playoff (weather) — Polymarket alphabetical-by-last-name, Kalshi silent — effectively never. Cross-exchange comparison is apples-to-apples; arb math is meaningful. **Gate cleared → integration unblocked.**

- [x] **Wire Polymarket into server.py** — DONE + verified. `refresh_poly_markets_loop()` + `run_poly_feed()` start in lifespan behind `POLY_ENABLED` flag (flip to False to fully disable). Fault-isolated: independent tasks + guarded loops + guarded `on_update` hook, so a Polymarket failure can't touch the Kalshi feed. Name→ticker bridge (`_rebuild_poly_map`, reuses leaderboard matching) maps poly condition_ids to Kalshi tickers; `polymarket_feed.on_update` broadcasts `type:"poly"` keyed by Kalshi ticker only when bid/ask moves. New `/api/poly` endpoint; init snapshot + `/api/markets` carry `poly_bid`/`poly_ask`/`poly_volume`. Verified: both feeds coexist (62 ticker + 71 poly broadcasts in 12s), 71/72 mapped, Kalshi unaffected.

- [x] **K/P columns in dashboard** — DONE. First built as stacked two-line cells, then (per Charlie) split into **separate columns**: K Bid | P Bid | K Ask | P Ask | K Vol | P Vol, all individually sortable, live-updated via targeted DOM. ⚠️ **Caveat:** the Volume columns mix units — Kalshi = contract count since server start (known limitation, see Phase 4 `count` task), Polymarket = cumulative USD ($k). Not directly comparable; revisit when Kalshi volume is fixed.

- [x] **Live speed / freshness tracker** — DONE (anti-swindle). New "Speed" column: per venue (K/P) a freshness dot (green <5s, yellow <30s, red ≥30s since last update) + updates/min over a rolling 60s window. Computed client-side from the message stream; a 1s timer repaints so freshness decays even with no new messages. Markets that aren't moving show red — you can see at a glance whether a quote is live before trusting it.

- [x] **Arb indicator column** — DONE. New "Arb" column driven by a JS port of `arbitrage.best_arb()` (verified to match the Python: net = gross − Kalshi fee). Shows best direction + net edge in ¢; net-positive rows highlight green. **Freshness-aware:** if either venue's quote is stale (>30s) the edge is shown amber with a ⚠ and the row is NOT highlighted — ties directly into the speed tracker so a stale quote can't masquerade as free money. Sortable by net edge. Keep the JS port in sync with `arbitrage.py`.

---

## Phase 3.5 — Make it feel alive · best-odds · cleaner charts (CURRENT FOCUS — confirmed do-first)

Charlie's goal: get every data source flowing and presented well. Descriptive/real-time only — **no predictive model now** (that's deferred, see Phase 5). **Decided: do this phase BEFORE 3.6.** Suggested build order within the phase: (1) capture `last_trade_price` + single-price column, (2) best-odds highlight, (3) detail-chart fix, (4) volume-units fix.

- [ ] **Polymarket single "price" column (highlighted)** — Polymarket's app shows ONE number, not a bid/ask. Add a visually highlighted column with that number. Plan: use the live **midpoint** = (poly_bid + poly_ask)/2 (matches their implied-probability display; updates whenever the book moves), optionally toggle to **last trade price**. Add a Kalshi mid alongside so the single numbers are directly comparable.

- [ ] **Capture `last_trade_price` (both venues)** — Polymarket's WS sends `last_trade_price` on every trade and we currently IGNORE it; Kalshi trades carry price too. Capturing it (a) feeds the single-price column and (b) makes the number tick as often as Polymarket's own app. ⚠️ Reality check: both feeds are ALREADY real-time WebSockets — there is no polling bottleneck on prices (ESPN 30s + Gamma 10min are metadata only). "Update faster" = surface the trade-frequency number, not re-architect.

- [ ] **Best-odds / line-shopping highlight (where Polymarket beats Kalshi)** — NOT arb. Per player, highlight which venue gives the better price: best to BUY YES = min(K ask, P ask); best to SELL YES = max(K bid, P bid). Color the winning venue's cell + a "Best: P/K" cue. This is the everyday "who has the better odds right now" signal — far more often actionable than risk-free arb.

- [ ] **Fix the detail (big) chart** — Root cause confirmed: `openDetail` plots the ENTIRE in-memory deque (up to 300 ticks ≈ 20+ min at golf tick rates) with no time trim, while the small sparkline uses ~20 points — so the big chart looks long/noisy (see Tommy Fleetwood). Plan: (a) trim to a real, labeled window + range buttons (5m / 30m / all); (b) drop garbage / duplicate-timestamp points causing vertical staircases; (c) **overlay Polymarket price history** so you can SEE K vs P diverge (needs `/api/poly-history/{ticker}` backed by `poly_ticks`); (d) show P bid/ask/price in the detail panel too.

- [ ] **Fix Kalshi volume units** — Kalshi "volume" is currently `trade_counts` since server start, while Polymarket is cumulative USD — K Vol vs P Vol is apples-to-oranges. Use Kalshi `volume_fp` / `volume_24h_fp` from REST so the columns are comparable. (Was the old Phase 4 "count" task — it's data plumbing, lives here now.)

---

## Phase 3.6 — Player performance / recent form (NEW data source)

Goal: quantify how well each player is ACTUALLY playing — last 1 / last 3 / last 5 starts — and normalize across the field (z-score) to help judge a player's "proper value." Descriptive analytics, **not** a model.

- [ ] **Research spike: what recent-form data can we actually get?** — **Decided: ESPN-first (free).** Standalone probe (like the Polymarket one): try ESPN's golf API for per-player recent results (athlete results / event history). Output a coverage report: how many of our 72 players we can get form for, and which fields (finish position, scoring avg, strokes-gained if available). Only if ESPN proves too thin do we revisit **DataGolf** (paid, strokes-gained) as an upgrade — not now.

- [ ] **`form.py` module** — Pull recent results per player; compute last-1 / last-3 / last-5 metrics (avg finish, scoring avg, …) and a **z-score normalized across the field**; store in a new SQLite table; refresh ~daily (form changes slowly). Match players via the same `leaderboard.py` fuzzy logic.

- [ ] **Form columns + detail** — Add recent-form columns (e.g., L5 finish, form z-score) to the table + a fuller breakdown in the detail panel.

- [ ] **(Optional) in-tournament momentum** — Reuse the live ESPN leaderboard (position/score/thru over time) as a current-event momentum signal.

---

## Phase 5 — Predictive model (DEFERRED — way down the line, per Charlie)

Explicitly NOT now. Get all data sources flowing + well-presented first. Recorded only so the eventual path exists:
- Feature engineering from `kalshi_data.db` + `poly_ticks` + leaderboard + form.
- Baseline model comparing predicted vs market-implied probability to flag mispricings.

---

## Ongoing / Housekeeping

- [ ] Update `EVENT_TICKER` in `feed.py` at the start of each new tournament.
- [ ] Archive `kalshi_data.db` after each tournament with the event ticker as a suffix (e.g., `kalshi_data_THMTPBW26.db`).
- [ ] Consider adding a `/api/raw-trade-sample` endpoint that dumps the last 5 raw trade messages — useful for debugging field names on new tournaments.
