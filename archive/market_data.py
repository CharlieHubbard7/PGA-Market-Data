import requests
from collections import defaultdict

BASE_URL = "https://external-api.kalshi.com/trade-api/v2"

def get_pga_markets(status="open", limit=200):
    url = f"{BASE_URL}/markets"
    params = {"series_ticker": "KXPGATOUR", "status": status, "limit": limit}
    response = requests.get(url, params=params)
    response.raise_for_status()
    return response.json()["markets"]

def print_markets_by_event(markets):
    by_event = defaultdict(list)
    for m in markets:
        by_event[m["event_ticker"]].append(m)

    for event_ticker, event_markets in by_event.items():
        print(f"\n=== {event_ticker} ===")
        # Sort by yes_bid descending (highest probability first)
        event_markets.sort(key=lambda m: m.get("yes_bid", 0), reverse=True)
        for m in event_markets:
            yes_bid = m.get("yes_bid", 0)
            volume = m.get("volume", 0)
            print(f"  {m['ticker']}")
            print(f"  {m['title']}")
            print(f"  Yes Bid: ${yes_bid:.4f} | Volume: ${volume:,.0f}")
            print()

if __name__ == "__main__":
    markets = get_pga_markets()
    print(f"Found {len(markets)} open PGA Tour markets")
    print_markets_by_event(markets)
