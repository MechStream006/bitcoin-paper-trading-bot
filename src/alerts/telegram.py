"""Minimal Telegram sender. Hits the bot HTTP API directly to keep Layer 1 dependency-light.
Will swap to python-telegram-bot in Layer 6 when we need command handlers (/positions, /pnl, etc.).
"""
import requests


def send_message(bot_token: str, chat_id: str, text: str, parse_mode: str | None = None) -> dict:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload: dict = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    r = requests.post(url, json=payload, timeout=10)
    r.raise_for_status()
    return r.json()
