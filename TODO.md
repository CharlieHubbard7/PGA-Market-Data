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

- [x] **Polymarket single "price" column (highlighted)** — DONE. **`poly_price` uses Polymarket's exact displayed-price rule** (confirmed by Charlie): midpoint `(best_bid+best_ask)/2` when spread ≤ **$0.10**, else the **last traded price**. Implemented in `server._poly_record`, computed live from WS best bid/ask. Verified across cases: Scheffler/Rory/Poston use mid; Coody (no bid → 0.1¢) and McCarty (15¢ spread → bid) use the fallback. 🔬 Detour logged: microprice + local order-book reconstruction both FAILED (size walls / phantom asks) — reverted to the `best_bid`/`best_ask` fields Polymarket streams directly. *(Optional later: periodic Gamma/`/midpoint` reconciliation to self-heal the rare stale best-ask on a dead market.)*

- [x] **K Price column** — DONE. Mirrors how Kalshi's site shows a price (and the Polymarket rule): two-sided book → midpoint `(bid+ask)/2`; one-sided book → `last_price_dollars` (the longshot-with-an-ask-but-no-bid case); else the single available side. 🐛 **Two bugs found & fixed along the way:** (1) the original halve-the-ask gave meaningless sub-cent values for the 30 no-bid longshots — fixed with the last-trade fallback (0 nulls across 72); (2) the Kalshi WS sends bid/ask/price as **strings**, and `feed._on_ticker` broadcast them un-cast, so `(bid+ask)/2` did string concatenation → NaN the instant the first live tick arrived ("correct on load, null once data comes in"). Fixed by `float()`-ing in `feed._on_ticker`/`_on_trade` (matches the snapshot + the CLAUDE.md note) plus a defensive `Number()` in the browser.

- [x] **Confidence band on the price (P + K)** — DONE. Spread-width CSS dims the cell: tight = full opacity, medium = `.conf-medium`, wide/one-sided = `.conf-wide` (italic+grey). A one-sided book is treated as low-confidence regardless of nominal spread. The anti-swindle signal Charlie picked (option #3).

- [x] **Blended "True" price column** — DONE. `truePrice` = K Price and P Price weighted by each venue's recent update rate (`rateMin`), so a stale venue is down-weighted and the live one dominates. Cross-venue consensus; K/P divergence from True = the "who's mispricing" signal.

- [x] **Collapse table to K Price · P Price · True** — DONE. Dropped K Bid / P Bid / K Ask / P Ask from the table; bid/ask now live in the detail panel (K Bid/Ask, P Bid/Ask, plus K Price & P Price readouts). Default sort = K Price desc.

- [x] **Closeable side panel + wider table** — DONE. The detail panel is now a fixed overlay drawer (slides in from the right, doesn't compress the table) with a ✕ close button + Esc-to-close, so the table keeps full width for the 3.6 columns. Cell padding bumped for readability.

- [x] **Fix the detail (big) chart** — DONE. Rebuilt as two clean price lines — **K Price (blue) + P Price (purple) overlaid** so you can see the venues diverge — backed by the new `/api/poly-history/{ticker}` (in-memory `poly_ticks`). De-noised via `dedupeTicks` (sort + drop duplicate timestamps). **5m / 30m / All range buttons.** Updates **live in real time for both venues** (K on each ticker, P on each poly update). Replaces the old bid/ask staircase.

- [x] **Capture `last_trade_price` (Polymarket)** — DONE. `polymarket_feed._on_message` now handles `last_trade_price` events → updates `yes_last` and broadcasts, so the number ticks on trades (not just book moves) and the Speed tracker counts trades too. `yes_last` + `yes_mid` now flow through `_emit`/broadcast/snapshot/`/api/markets`/`/api/poly`. Verified: 29/65 poly broadcasts carried `poly_last` in 14s. ⚠️ Reality check confirmed: both feeds were ALREADY real-time WS — no polling bottleneck; this just surfaces the trade-frequency number. *(Kalshi last-trade capture not done yet — Kalshi already has its own bid/ask flashes; revisit if we want a Kalshi single-price too.)*

- [x] **Kalshi volume units** — DONE (resolved by Charlie's call): K Vol is a clean **trade count since server start**, NOT a dollar figure. Tried surfacing REST `volume_fp` but its unit is ambiguous (values ~10^5–10^6, not dollars) and a live `price×count` running total was a misleading money *estimate* — Charlie asked to drop the estimate and just keep the count. K Vol and P Vol remain different units (count vs cumulative USD); not directly comparable, and that's fine for now.

- [ ] **(moved to bottom) Best-odds / line-shopping highlight** — Deprioritized per Charlie. NOT arb. Per player, highlight which venue gives the better price: best to BUY YES = min(K ask, P ask); best to SELL YES = max(K bid, P bid). Color the winning venue's cell + a "Best: P/K" cue. *(A lightweight version already ships: the `.best-ask` green border marks whichever of K Price / P Price has the lower ask. A fuller treatment can come after 3.6.)*

---

## Phase 3.6 — Player performance / recent form (NEW data source)

Goal: quantify how well each player is ACTUALLY playing — last 1 / last 3 / last 5 starts — and normalize across the field (z-score) to help judge a player's "proper value." Descriptive analytics, **not** a model.

- [x] **Research spike: what recent-form data can we actually get?** — ✅ DONE in `form_probe.py` (standalone). **ESPN is plenty — 72/72 coverage on every field, free, no auth.** Findings:
  - **Athlete ids for free:** the PGA `scoreboard` competitor `id` IS the ESPN athlete id (10166 → J.J. Spaun). Since the scoreboard = the live field, all 72 of our players map directly — no name search needed for ids.
  - **One endpoint has it all:** `site.web.api.espn.com/apis/common/v3/sports/golf/pga/athletes/{id}/overview` (one GET/player) →
    - `recentTournaments[0].eventsStats[]` — last ~5 starts, each with `competitions[0].competitors[0].status.position.displayName` (finish, e.g. `T2`). → **L1 / avg-L3 / avg-L5 finish.**
    - `statistics.splits` (PGA TOUR row) — **events, cuts made, top-10s, wins, scoring avg.** → made-cut %.
    - `seasonRankings.categories` — **driving distance, driving accuracy %, greens hit, putts/GIR, birdies/round** (each w/ a PGA-Tour rank) + **money-won rank** (`amount`). These are the best free **Strokes-Gained proxies**.
  - **Coverage (of 72): 72/72** on L1/L3/L5 finish, made-cut %, top10, wins, events, earnings rank, and all 5 SG-proxy categories. **70/72** on PGA-TOUR scoring avg (only Rory McIlroy + Alex Fitzpatrick missing — their events sit under a non-PGA split; we still have their finishes/everything else).
  - **NOT available free:** Strokes Gained (total/OTT/APP/ARG/PUTT) and OWGR — both DataGolf (paid). The SG proxies above + recent finishes are the free substitute. **Verdict: ESPN is strong enough; do NOT pay for DataGolf now.**
  - **Field-wide z-scores work** (computed in the probe): scoring-avg-z and avg-L5-finish-z (both inverted so high = good). Top of the form board reads sensibly: Scheffler, Spaun, Åberg, Clark.
  - ⚠️ **Note for form.py:** the live in-progress tournament shows up in `eventsStats` with a moving position — exclude the current event id explicitly (don't rely only on `status.state=='post'`) so L1/L3/L5 are completed starts only.

- [x] **`form.py` module** — DONE. Promoted the probe's fetch/parse into a module mirroring `leaderboard.py`: `build_form()` (threaded ESPN pull + parse + field z-scores + composite `form_score` = mean of avg-L5-z & scoring-avg-z), `refresh_form_loop()` (every 6h — form moves between events, not minute-to-minute), `_apply()` annotates `feed.markets` in place + returns the per-ticker map. New SQLite `form` table + `db.write_form()`. Wired into `server.py` lifespan behind the `on_update=_broadcast_form` hook; broadcasts `type:"form"` (whole map). New `/api/form` endpoint; form embedded in `/api/markets` + WS snapshot. Excludes the in-progress event so L1/L3/L5 are completed starts only. Verified: 72/72 matched, Scheffler form 2.44 (GIR/putt/birdies rank #1).

- [x] **Form columns + detail** — DONE, then EXPANDED into a wide Excel-style sheet (per Charlie). The table is now horizontally scrollable with a **frozen Player column** (`col-sticky`), and carries the full data set:
  - **Leaderboard split:** Pos · **Rd** (R1–R4) · **Thru** (hole) · **Score** — separate columns (was one combined cell). `leaderboard._round_thru` derives round + thru from the per-round linescores; `round` flows through the wire/markets/snapshot.
  - **Form block:** Form (composite z, color-graded) · **L1 · L3 · L5** (last-1/3/5 finishes, split out) · Cut%.
  - **Season skill stats, each its own column** (Charlie's "strokes gained / putting / GIR and stuff", value + tour rank): **ScAvg · GIR% · Putts/GIR · Drive · Acc · Bird · Sand**. (ESPN has no true SG — these are the free season stats; GIR% is the recent-tournament avg since ESPN's season GIR is a raw count.)
  - All sortable; detail panel shows the full Recent-Form + Season-Skills breakdown with tour ranks + money rank.
  - New `form` SQLite columns + migration for the skill values.

- [x] **OWGR world ranking** — DONE. Pulled live from the **modern owgr.com JSON API** (`apiweb.owgr.com/api/owgr/rankings/getRankings?pageSize=1000&...`) — NOT the old BeautifulSoup HTML scraper (obsolete; the site is now a SPA with a clean API). `form._fetch_owgr`/`_attach_owgr` matches the top-1000 to the field via the leaderboard fuzzy matcher: **72/72** (Scheffler #1, Rory #2, C.Young #3). Flows through the form wire / markets / snapshot / `form` SQLite column (migration added). ESPN itself has no world ranking — owgr.com is the source. Column reordered per Charlie: **Player · Pos · Rd · Thru · Score · True · Form · Speed · 5m Trend · OWGR**, then K Price · P Price · L1 · L3 · L5 · Cut% · ScAvg · GIR% · Putt · Drive · Acc · Bird · Sand · K Vol · P Vol · Arb. OWGR styled as a headline (blue) column.

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
