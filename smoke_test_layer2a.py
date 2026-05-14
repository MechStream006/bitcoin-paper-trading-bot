"""Layer 2a smoke test.

Connects to the Delta testnet WebSocket, subscribes to the BTCUSD perpetual
ticker, prints the first ~10 messages, then disconnects.

Run from the project root:
    python smoke_test_layer2a.py

Done condition (Layer 2a):
  Console shows live ticker messages with a price field for BTCUSD.

Notes:
  - Uses websocket-client (sync) per the architectural decision in this layer.
  - No DB writes yet — that's 2c. We're just proving the pipe.
  - No heartbeat / reconnect logic — that's the real delta_ws.py module in 2b/2c.
"""
import json

from websocket import create_connection

from config.settings import DELTA_WS_URL

SYMBOL = "BTCUSDT"
N_MESSAGES = 10
RECV_TIMEOUT_SEC = 30


def summarize(msg: dict) -> str:
    msg_type = msg.get("type", "?")
    if msg_type == "v2/ticker":
        return (
            f"ticker {msg.get('symbol')} "
            f"mark={msg.get('mark_price')} "
            f"close={msg.get('close')} "
            f"ts={msg.get('timestamp')}"
        )
    return f"{msg_type}: {json.dumps(msg)[:200]}"


def main() -> None:
    print(f"[1/3] Connecting to {DELTA_WS_URL} ...")
    ws = create_connection(DELTA_WS_URL, timeout=10)
    ws.settimeout(RECV_TIMEOUT_SEC)
    print("      Connected.")

    print(f"[2/3] Subscribing to v2/ticker for {SYMBOL} ...")
    sub = {
        "type": "subscribe",
        "payload": {
            "channels": [
                {"name": "v2/ticker", "symbols": [SYMBOL]},
            ]
        },
    }
    ws.send(json.dumps(sub))

    print(f"[3/3] Receiving first {N_MESSAGES} messages ...")
    for i in range(N_MESSAGES):
        raw = ws.recv()
        try:
            msg = json.loads(raw)
            print(f"  [{i+1:>2}] {summarize(msg)}")
        except json.JSONDecodeError:
            print(f"  [{i+1:>2}] (raw) {raw[:200]}")

    ws.close()
    print("Done. WS closed cleanly.")


if __name__ == "__main__":
    main()
