"""Versand von Alert-Nachrichten über die Telegram Bot API.

Optional: Ohne TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID in der .env tut
send_telegram_message() einfach nichts - kein Crash, der Rest des Scanners
läuft unverändert weiter (siehe config.py TELEGRAM_ENABLED).
"""
from __future__ import annotations

import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_ENABLED

REQUEST_TIMEOUT_SECONDS = 10


class TelegramError(RuntimeError):
    """Wird ausgelöst, wenn der Versand über die Telegram-API fehlschlägt."""


def send_telegram_message(text: str) -> None:
    if not TELEGRAM_ENABLED:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}

    try:
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise TelegramError(f"Telegram-Request fehlgeschlagen: {exc}") from exc

    if response.status_code != 200:
        raise TelegramError(f"Telegram -> HTTP {response.status_code}: {response.text[:200]}")
