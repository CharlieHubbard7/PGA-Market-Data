import asyncio
import json
import time
import base64
import ssl
import certifi
import websockets
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

import config

WS_URL = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"

# PGA Tour markets to watch — pull from market_data.py or hardcode tickers here
# Leave empty to subscribe to all ticker updates (high volume)
MARKET_TICKERS = [
    "KXPGATOUR-THMTPBW26-SSCH",  # Scottie Scheffler - Memorial
    "KXPGATOUR-THMTPBW26-RMCI",  # Rory McIlroy - Memorial
    "KXPGATOUR-THMTPBW26-LABE",  # Ludvig Åberg - Memorial
    "KXPGATOUR-THMTPBW26-CAME",  # Cameron Young - Memorial
]


def build_auth_headers(private_key) -> dict:
    timestamp_ms = str(int(time.time() * 1000))
    message = timestamp_ms + "GET" + "/trade-api/ws/v2"
    signature = private_key.sign(
        message.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return {
        "KALSHI-ACCESS-KEY": config.API_KEY,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
        "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
    }


def handle_ticker(msg: dict):
    t = msg.get("market_ticker", "?")
    yes_bid = msg.get("yes_bid_dollars", "—")
    yes_ask = msg.get("yes_ask_dollars", "—")
    print(f"[TICKER] {t}  bid=${yes_bid}  ask=${yes_ask}")


def handle_trade(msg: dict):
    t = msg.get("market_ticker", "?")
    side = msg.get("taker_side", "?")
    price = msg.get("yes_price_dollars", "?")
    count = msg.get("count", "?")
    print(f"[TRADE]  {t}  side={side}  price=${price}  contracts={count}")


HANDLERS = {
    "ticker": handle_ticker,
    "trade": handle_trade,
}


async def subscribe(ws, channels: list[str], tickers: list[str]):
    payload = {"id": 1, "cmd": "subscribe", "params": {"channels": channels}}
    if tickers:
        payload["params"]["market_tickers"] = tickers
    await ws.send(json.dumps(payload))


async def run():
    private_key = config.load_private_key()
    headers = build_auth_headers(private_key)

    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    print(f"Connecting to {WS_URL} ...")
    async with websockets.connect(WS_URL, additional_headers=headers, ssl=ssl_ctx) as ws:
        print("Connected. Subscribing to ticker + trade channels...")
        await subscribe(ws, channels=["ticker", "trade"], tickers=MARKET_TICKERS)

        async for raw in ws:
            envelope = json.loads(raw)
            msg_type = envelope.get("type")

            if msg_type == "subscribed":
                print(f"[OK] Subscribed to: {envelope.get('msg', {}).get('channels', [])}")
            elif msg_type == "error":
                err = envelope.get("msg", {})
                print(f"[ERROR] code={err.get('code')}  msg={err.get('msg')}")
            elif msg_type in HANDLERS:
                HANDLERS[msg_type](envelope.get("msg", {}))
            else:
                # Catch-all for orderbook snapshots, lifecycle events, etc.
                print(f"[{msg_type}] {json.dumps(envelope.get('msg', {}))}")


async def run_with_reconnect():
    backoff = 1
    while True:
        try:
            await run()
        except (websockets.ConnectionClosed, OSError) as e:
            print(f"Connection lost: {e}. Reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
        else:
            backoff = 1


if __name__ == "__main__":
    asyncio.run(run_with_reconnect())
