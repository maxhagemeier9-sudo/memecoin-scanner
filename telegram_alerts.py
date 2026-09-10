"""Versand von Alert-Nachrichten über die Telegram Bot API.

Optional: Ohne TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID in der .env tut
send_telegram_message() einfach nichts - kein Crash, der Rest des Scanners
läuft unverändert weiter (siehe config.py TELEGRAM_ENABLED).

Ein Scan-Zyklus kann mehrere Nachrichten kurz hintereinander verschicken
(Zusammenfassung, mehrere Alerts, Paper-Trade-Report) - ohne Pause dazwischen
reißt das leicht Telegrams Rate-Limit (ca. 1 Nachricht/Sekunde pro Chat) und
jede weitere Nachricht bekommt sofort wieder HTTP 429 (live in den CI-Logs
beobachtet: ALLE Sends eines Laufs schlugen fehl, sobald der erste
429 kam). Telegram schickt im 429-Body die exakte Wartezeit
(`parameters.retry_after`) - die wird hier respektiert und danach einmal
automatisch erneut versucht, statt sofort aufzugeben.
"""
from __future__ import annotations

import time

import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_ENABLED

REQUEST_TIMEOUT_SECONDS = 10
MAX_RETRIES = 3
DEFAULT_RETRY_AFTER_SECONDS = 5


class TelegramError(RuntimeError):
    """Wird ausgelöst, wenn der Versand über die Telegram-API fehlschlägt."""


def _parse_retry_after(response: requests.Response) -> float:
    """Liest parameters.retry_after aus dem 429-Body, mit Fallback für den
    Fall, dass die Antwort mal nicht dem erwarteten JSON-Format entspricht."""
    try:
        return float(response.json().get("parameters", {}).get("retry_after", DEFAULT_RETRY_AFTER_SECONDS))
    except (ValueError, TypeError):
        return DEFAULT_RETRY_AFTER_SECONDS


def send_telegram_message(text: str, parse_mode: str | None = None) -> None:
    if not TELEGRAM_ENABLED:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            raise TelegramError(f"Telegram-Request fehlgeschlagen: {exc}") from exc

        if response.status_code == 200:
            return

        if response.status_code == 429 and attempt < MAX_RETRIES:
            time.sleep(_parse_retry_after(response))
            continue

        raise TelegramError(f"Telegram -> HTTP {response.status_code}: {response.text[:200]}")
