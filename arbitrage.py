"""
Cross-exchange arbitrage math — Phase 3, step 3.

STANDALONE + TESTED. Not wired into server.py. Run directly:

    python3 arbitrage.py --test     # run the self-tests (no network)
    python3 arbitrage.py            # live scan: Kalshi vs Polymarket, sorted by edge

────────────────────────────────────────────────────────────────────────────
THE MODEL
Each "Will <player> win the Memorial?" market is a binary contract paying $1 if
true, $0 if false, priced in [0,1]. The SAME outcome trades on both Kalshi and
Polymarket. We hold YES bid/ask on each venue, where:
    yes_ask = lowest price we can BUY YES at
    yes_bid = highest price we can SELL YES at   (== buy NO at 1 - yes_bid)

A risk-free position pays exactly $1 while costing < $1:
    Direction A: BUY YES on Kalshi @ k_ask  +  BUY NO on Poly (= sell YES @ p_bid)
                 cost = k_ask + (1 - p_bid);  payout = $1
                 gross profit/contract = p_bid - k_ask
    Direction B: BUY YES on Poly @ p_ask     +  BUY NO on Kalshi (= sell YES @ k_bid)
                 gross profit/contract = k_bid - p_ask

So: an arb exists when one venue's BID > the other venue's ASK. We take the
better of the two directions, net of fees.
────────────────────────────────────────────────────────────────────────────
ASSUMPTIONS (each one is a way the "risk-free" edge could be wrong):

  Market equivalence
    1. SAME outcome + SAME resolution rules across venues — identical handling
       of playoffs, ties/dead-heats, withdrawals, DQs. *Biggest unverified one.*
       (Next concrete task: diff Kalshi rules vs Polymarket `description`.)
    2. Name match == market match (67 paired via leaderboard.py fuzzy logic).

  Pricing & execution
    3. Top-of-book only — edge holds only for size at the best level; we have
       NO depth on the Polymarket side (Gamma gives just best bid/ask).
    4. Prices contemporaneous — both feeds are now real-time WebSockets, so
       staleness is sub-second (was up to 30s under the old polling feed).
    5. Both legs fill at the quoted price, simultaneously, no slippage/partials.
    6. Buying NO == 1 − YES bid (standard binary identity).

  Fees & costs
    7. Kalshi fee = ceil(0.07·C·P·(1−P)) per order, modeled at a 100-contract
       size (1-lots pay proportionally more due to the cent round-up). 7% rate.
    8. Polymarket fees = 0 (historically true here; configurable via p_rate).
    9. Ignored: Polygon gas, USDC↔USD peg (assumed 1:1), capital cost to
       resolution.

  Scope
    10. Only the "Winner" market is compared (Top-5/10/20 are out of scope).
────────────────────────────────────────────────────────────────────────────
"""

import math


# ── fees ──────────────────────────────────────────────────────────────────────

def kalshi_fee_per_contract(price: float, contracts: int = 100, rate: float = 0.07) -> float:
    """Kalshi trading fee per contract.

    Kalshi's published formula: fee = ceil(rate * C * P * (1-P)), rounded UP to
    the cent on the WHOLE order. Symmetric in P (so YES@P and NO@1-P cost the
    same fee). The round-up means tiny single-contract trades pay a full cent;
    at size the per-contract fee approaches rate*P*(1-P). We default to 100
    contracts to model a realistic order, not a 1-lot.
    """
    if price is None or price <= 0 or price >= 1:
        return 0.0
    # round() before ceil() kills float noise (e.g. 0.07*100*.5*.5*100 == 175.0000000003,
    # which would otherwise ceil up to 176 and add a spurious 0.01¢).
    cents = math.ceil(round(rate * contracts * price * (1 - price) * 100, 6))
    return (cents / 100) / contracts


def poly_fee_per_contract(price: float, contracts: int = 100, rate: float = 0.0) -> float:
    """Polymarket trading fee per contract. Historically ~0 on these markets, so
    the default rate is 0. Configurable in case that changes."""
    if price is None:
        return 0.0
    return rate * price


# ── core (pure, testable) ────────────────────────────────────────────────────────

def best_arb(k: dict, p: dict, *, contracts: int = 100,
             k_rate: float = 0.07, p_rate: float = 0.0) -> dict | None:
    """Given matched Kalshi (k) and Polymarket (p) YES bid/ask, return the better
    of the two arb directions as a dict, or None if neither side is quotable.

    k, p each: {'yes_bid': float|None, 'yes_ask': float|None}  (prices in [0,1])
    Returns: {direction, buy_venue, buy_at, sell_venue, sell_at, gross, fee, net}
    'net' is profit per contract in dollars (can be negative = no arb).
    """
    opps = []

    # Direction A: buy YES cheap on Kalshi (k_ask), sell YES dear on Poly (p_bid)
    if _quotable(k.get("yes_ask")) and _quotable(p.get("yes_bid")):
        gross = p["yes_bid"] - k["yes_ask"]
        fee = (kalshi_fee_per_contract(k["yes_ask"], contracts, k_rate)
               + poly_fee_per_contract(1 - p["yes_bid"], contracts, p_rate))
        opps.append({
            "direction": "buy Kalshi / sell Polymarket",
            "buy_venue": "Kalshi", "buy_at": k["yes_ask"],
            "sell_venue": "Polymarket", "sell_at": p["yes_bid"],
            "gross": gross, "fee": fee, "net": gross - fee,
        })

    # Direction B: buy YES cheap on Poly (p_ask), sell YES dear on Kalshi (k_bid)
    if _quotable(p.get("yes_ask")) and _quotable(k.get("yes_bid")):
        gross = k["yes_bid"] - p["yes_ask"]
        fee = (poly_fee_per_contract(p["yes_ask"], contracts, p_rate)
               + kalshi_fee_per_contract(k["yes_bid"], contracts, k_rate))
        opps.append({
            "direction": "buy Polymarket / sell Kalshi",
            "buy_venue": "Polymarket", "buy_at": p["yes_ask"],
            "sell_venue": "Kalshi", "sell_at": k["yes_bid"],
            "gross": gross, "fee": fee, "net": gross - fee,
        })

    return max(opps, key=lambda o: o["net"]) if opps else None


def _quotable(price) -> bool:
    return price is not None and 0 < price < 1


# ── self-tests (no network) ────────────────────────────────────────────────────

def _run_tests():
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        if not cond:
            fails += 1

    # 1. Clear arb: Poly bid (0.25) > Kalshi ask (0.22) → Direction A, net > 0.
    a = best_arb({"yes_bid": 0.20, "yes_ask": 0.22}, {"yes_bid": 0.25, "yes_ask": 0.27})
    check("clear arb picks 'buy Kalshi / sell Polymarket'", a["direction"] == "buy Kalshi / sell Polymarket")
    check("clear arb gross == 0.03", abs(a["gross"] - 0.03) < 1e-9)
    check("clear arb net positive after fees", a["net"] > 0)

    # 2. No arb: books overlap normally (bids below the other's asks).
    n = best_arb({"yes_bid": 0.20, "yes_ask": 0.23}, {"yes_bid": 0.21, "yes_ask": 0.24})
    check("aligned books → net <= 0 (no free money)", n["net"] <= 0)

    # 3. Direction B: Kalshi bid (0.30) > Poly ask (0.26) → buy Poly / sell Kalshi.
    b = best_arb({"yes_bid": 0.30, "yes_ask": 0.33}, {"yes_bid": 0.24, "yes_ask": 0.26})
    check("reverse arb picks 'buy Polymarket / sell Kalshi'", b["direction"] == "buy Polymarket / sell Kalshi")
    check("reverse arb gross == 0.04", abs(b["gross"] - 0.04) < 1e-9)

    # 4. Fee symmetry: kalshi_fee(P) == kalshi_fee(1-P).
    check("kalshi fee symmetric in P", abs(kalshi_fee_per_contract(0.2) - kalshi_fee_per_contract(0.8)) < 1e-12)

    # 5. Fee sanity at P=0.5, size 100: 0.07*0.25 = 0.0175/contract.
    check("kalshi fee at P=0.5 ≈ 1.75¢/contract", abs(kalshi_fee_per_contract(0.5, 100) - 0.0175) < 1e-9)

    # 6. Missing quote on one side → that direction is dropped; None if neither.
    one = best_arb({"yes_bid": None, "yes_ask": 0.22}, {"yes_bid": 0.25, "yes_ask": None})
    check("only Direction A quotable", one is not None and one["direction"] == "buy Kalshi / sell Polymarket")
    check("no quotes → None", best_arb({"yes_bid": None, "yes_ask": None},
                                        {"yes_bid": None, "yes_ask": None}) is None)

    print(f"\n{'ALL TESTS PASSED' if not fails else f'{fails} TEST(S) FAILED'}")
    return fails == 0


# ── live scan ──────────────────────────────────────────────────────────────────

def _live_scan(contracts: int = 100):
    import feed
    import polymarket_feed
    from leaderboard import normalize_name, _fuzzy_key

    print(f"Fetching both venues (fee model: {contracts}-contract order, Kalshi 7%, Poly 0%)...\n")

    # Kalshi via REST (_dollars fields — see feed.refresh_markets_loop).
    kal = []
    for m in feed._fetch_markets():
        kal.append({
            "player":  feed._player_name(m.get("title", "")),
            "yes_bid": _f(m.get("yes_bid_dollars")),
            "yes_ask": _f(m.get("yes_ask_dollars")),
        })
    poly = polymarket_feed.fetch_markets()

    # Index Polymarket by name, match each Kalshi market (same logic as leaderboard).
    p_by_name, p_by_key = {}, {}
    for r in poly:
        p_by_name[normalize_name(r["player"])] = r
        key = _fuzzy_key(r["player"])
        if key:
            p_by_key.setdefault(key, r)

    rows = []
    for k in kal:
        p = p_by_name.get(normalize_name(k["player"])) or p_by_key.get(_fuzzy_key(k["player"]) or ("", ""))
        if not p:
            continue
        arb = best_arb(k, p, contracts=contracts)
        if arb:
            rows.append((k, p, arb))

    rows.sort(key=lambda r: -r[2]["net"])
    print(f"Matched & quotable on both venues: {len(rows)}\n")
    hdr = f"{'Player':20} | {'K bid/ask':>13} | {'P bid/ask':>13} | {'best direction':28} | {'gross':>6} {'fee':>6} {'net':>7}"
    print(hdr); print("-" * len(hdr))
    arbs = 0
    for k, p, a in rows:
        if a["net"] > 0:
            arbs += 1
        flag = "  <== ARB" if a["net"] > 0 else ""
        print(f"{k['player'][:20]:20} | {_cc(k['yes_bid'])}/{_cc(k['yes_ask'])} | "
              f"{_cc(p['yes_bid'])}/{_cc(p['yes_ask'])} | {a['direction']:28} | "
              f"{a['gross']*100:+5.1f}¢ {a['fee']*100:5.2f}¢ {a['net']*100:+6.2f}¢{flag}")
    print(f"\nNet-positive opportunities (after fees): {arbs} / {len(rows)}")
    print("Reminder: edges are TOP-OF-BOOK only (size unknown on Poly side); "
          "Polymarket prices may be up to ~30s stale vs Kalshi.")


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _cc(v):
    return f"{v*100:5.1f}¢" if v is not None else "   — "


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        _run_tests()
    else:
        _live_scan()
