"""Tests für die Alert-Mechanik - gemockt (kein echter Sound, kein echter
Telegram-Versand)."""
from unittest.mock import patch

from alerts import alert
from telegram_alerts import TelegramError


@patch("alerts.winsound", None)
@patch("alerts.send_telegram_message")
def test_alert_prints_message_and_calls_telegram(mock_send, capsys):
    alert("Test-Nachricht")
    captured = capsys.readouterr()
    assert "Test-Nachricht" in captured.out
    mock_send.assert_called_once()
    assert "Test-Nachricht" in mock_send.call_args[0][0]


@patch("alerts.winsound", None)
@patch("alerts.send_telegram_message", side_effect=TelegramError("boom"))
def test_alert_survives_telegram_failure(mock_send, capsys):
    alert("Test-Nachricht")  # darf nicht crashen
    captured = capsys.readouterr()
    assert "Test-Nachricht" in captured.out
    assert "fehlgeschlagen" in captured.out
