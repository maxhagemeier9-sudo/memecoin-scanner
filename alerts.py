"""Alert-Mechanik für den Monitor-Modus: Terminal-Beep + Zeitstempel + Telegram.

Nutzt winsound unter Windows (dieses Projekt läuft auf Windows, siehe
Systemumgebung), fällt sonst auf einen reinen Terminal-Bell zurück. Telegram
ist optional (siehe telegram_alerts.py) - ein Fehlschlag dort lässt den
Rest des Alerts unberührt.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from telegram_alerts import TelegramError, send_telegram_message

try:
    import winsound
except ImportError:
    winsound = None


def alert(message: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"\a🚨 [{timestamp} UTC] {message}")
    sys.stdout.flush()

    if winsound is not None:
        try:
            winsound.Beep(1000, 300)
        except RuntimeError:
            pass  # z.B. kein Audio-Gerät verfügbar - Beep ist ein Nice-to-have

    try:
        send_telegram_message(f"🚨 {message}")
    except TelegramError as exc:
        print(f"   (Telegram-Alert fehlgeschlagen: {exc})")
