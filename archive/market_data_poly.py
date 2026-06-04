import requests

# Step 1: Find all golf series/events
base = "https://external-api.kalshi.com/trade-api/v2"
events = requests.get(f"{base}/events", params={"category": "Sports", "limit": 200}).json()

golf_events = [e for e in events["events"] if "golf" in e.get("title", "").lower() 
               or "golf" in str(e.get("tags", [])).lower()]

# Step 2: For each, grab all markets + candlesticks
for event in golf_events:
    markets = requests.get(f"{base}/markets", 
                           params={"event_ticker": event["event_ticker"]}).json()
    for mkt in markets["markets"]:
        candles = requests.get(f"{base}/markets/{mkt['ticker']}/candlesticks",
                               params={"period_interval": "1h"}).json()