"""Tests für Telegram-Alerts - gemockt, kein echter Versand."""
from unittest.mock import Mock, patch

import pytest

from telegram_alerts import TelegramError, send_telegram_message


@patch("telegram_alerts.TELEGRAM_ENABLED", False)
@patch("telegram_alerts.requests.post")
def test_disabled_does_nothing(mock_post):
    send_telegram_message("test")
    mock_post.assert_not_called()


@patch("telegram_alerts.TELEGRAM_ENABLED", True)
@patch("telegram_alerts.TELEGRAM_BOT_TOKEN", "fake-token")
@patch("telegram_alerts.TELEGRAM_CHAT_ID", "fake-chat")
@patch("telegram_alerts.requests.post")
def test_successful_send_calls_correct_url_and_payload(mock_post):
    resp = Mock()
    resp.status_code = 200
    mock_post.return_value = resp

    send_telegram_message("hello world")

    mock_post.assert_called_once()
    url, kwargs = mock_post.call_args[0][0], mock_post.call_args[1]
    assert url == "https://api.telegram.org/botfake-token/sendMessage"
    assert kwargs["json"] == {"chat_id": "fake-chat", "text": "hello world"}


@patch("telegram_alerts.TELEGRAM_ENABLED", True)
@patch("telegram_alerts.TELEGRAM_BOT_TOKEN", "fake-token")
@patch("telegram_alerts.TELEGRAM_CHAT_ID", "fake-chat")
@patch("telegram_alerts.requests.post")
def test_http_error_raises_telegram_error(mock_post):
    resp = Mock()
    resp.status_code = 400
    resp.text = "chat not found"
    mock_post.return_value = resp

    with pytest.raises(TelegramError):
        send_telegram_message("hello")


@patch("telegram_alerts.TELEGRAM_ENABLED", True)
@patch("telegram_alerts.TELEGRAM_BOT_TOKEN", "fake-token")
@patch("telegram_alerts.TELEGRAM_CHAT_ID", "fake-chat")
@patch("telegram_alerts.requests.post")
def test_network_exception_raises_telegram_error(mock_post):
    import requests

    mock_post.side_effect = requests.ConnectionError("boom")

    with pytest.raises(TelegramError):
        send_telegram_message("hello")


@patch("telegram_alerts.time.sleep")
@patch("telegram_alerts.TELEGRAM_ENABLED", True)
@patch("telegram_alerts.TELEGRAM_BOT_TOKEN", "fake-token")
@patch("telegram_alerts.TELEGRAM_CHAT_ID", "fake-chat")
@patch("telegram_alerts.requests.post")
def test_rate_limit_is_retried_after_the_wait_time_telegram_specifies(mock_post, mock_sleep):
    rate_limited = Mock(status_code=429)
    rate_limited.json.return_value = {"parameters": {"retry_after": 7}}
    ok = Mock(status_code=200)
    mock_post.side_effect = [rate_limited, ok]

    send_telegram_message("hello")

    assert mock_post.call_count == 2
    mock_sleep.assert_called_once_with(7.0)


@patch("telegram_alerts.time.sleep")
@patch("telegram_alerts.TELEGRAM_ENABLED", True)
@patch("telegram_alerts.TELEGRAM_BOT_TOKEN", "fake-token")
@patch("telegram_alerts.TELEGRAM_CHAT_ID", "fake-chat")
@patch("telegram_alerts.requests.post")
def test_rate_limit_gives_up_after_max_retries(mock_post, mock_sleep):
    rate_limited = Mock(status_code=429, text='{"error_code":429}')
    rate_limited.json.return_value = {"parameters": {"retry_after": 1}}
    mock_post.return_value = rate_limited

    with pytest.raises(TelegramError):
        send_telegram_message("hello")

    assert mock_post.call_count == 4  # 1 initialer Versuch + 3 Retries
    assert mock_sleep.call_count == 3


@patch("telegram_alerts.time.sleep")
@patch("telegram_alerts.TELEGRAM_ENABLED", True)
@patch("telegram_alerts.TELEGRAM_BOT_TOKEN", "fake-token")
@patch("telegram_alerts.TELEGRAM_CHAT_ID", "fake-chat")
@patch("telegram_alerts.requests.post")
def test_rate_limit_without_parseable_retry_after_uses_default_wait(mock_post, mock_sleep):
    rate_limited = Mock(status_code=429)
    rate_limited.json.side_effect = ValueError("not json")
    ok = Mock(status_code=200)
    mock_post.side_effect = [rate_limited, ok]

    send_telegram_message("hello")

    mock_sleep.assert_called_once_with(5)
