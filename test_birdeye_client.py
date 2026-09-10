"""Integrationstests für BirdeyeClient: Retry-/Error-Handling, gemockt
(unittest.mock, stdlib - keine Live-Calls, keine neue Dependency).

Deckt genau die Schicht ab, die bisher nur "live" getestet wurde - dort
saß z.B. der Bug, dass /defi/v2/tokens/new_listing bei limit>20 mit HTTP 400
antwortet statt der vermuteten 429.
"""
from unittest.mock import Mock, patch

import pytest
import requests

from birdeye_client import BirdeyeAPIError, BirdeyeClient


def _response(status_code, json_data=None, text=""):
    resp = Mock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text
    return resp


def _client():
    return BirdeyeClient(api_key="test-key", chain="solana")


@patch("birdeye_client.requests.Session.get")
def test_successful_call_returns_parsed_data(mock_get):
    mock_get.return_value = _response(200, {"success": True, "data": {"tokens": [{"symbol": "ABC"}]}})
    tokens = _client().get_token_list()
    assert tokens == [{"symbol": "ABC"}]
    assert mock_get.call_count == 1


@patch("birdeye_client.time.sleep", return_value=None)
@patch("birdeye_client.requests.Session.get")
def test_429_is_retried_and_succeeds_on_second_attempt(mock_get, mock_sleep):
    mock_get.side_effect = [
        _response(429, text="rate limited"),
        _response(200, {"success": True, "data": {"tokens": []}}),
    ]
    tokens = _client().get_token_list()
    assert tokens == []
    assert mock_get.call_count == 2


@patch("birdeye_client.time.sleep", return_value=None)
@patch("birdeye_client.requests.Session.get")
def test_429_exhausts_retries_and_raises(mock_get, mock_sleep):
    mock_get.return_value = _response(429, text="rate limited")
    with pytest.raises(BirdeyeAPIError):
        _client().get_token_list()
    assert mock_get.call_count == 3  # MAX_RETRIES aus config.py


@patch("birdeye_client.requests.Session.get")
def test_non_retryable_error_raises_immediately_without_retry(mock_get):
    mock_get.return_value = _response(400, text="limit should be integer, range 1-20")
    with pytest.raises(BirdeyeAPIError):
        _client().get_new_listings(limit=30)
    assert mock_get.call_count == 1


@patch("birdeye_client.time.sleep", return_value=None)
@patch("birdeye_client.requests.Session.get")
def test_network_exception_is_retried(mock_get, mock_sleep):
    mock_get.side_effect = [
        requests.ConnectionError("boom"),
        _response(200, {"success": True, "data": {"tokens": []}}),
    ]
    tokens = _client().get_token_list()
    assert tokens == []
    assert mock_get.call_count == 2


@patch("birdeye_client.time.sleep", return_value=None)
@patch("birdeye_client.requests.Session.get")
def test_server_error_is_retried_then_raises(mock_get, mock_sleep):
    mock_get.return_value = _response(500, text="internal error")
    with pytest.raises(BirdeyeAPIError):
        _client().get_market_data("addr")
    assert mock_get.call_count == 3


@patch("birdeye_client.requests.Session.get")
def test_get_holders_returns_data_payload(mock_get):
    mock_get.return_value = _response(200, {
        "success": True,
        "data": {"holder": 1000, "top10_hold_percent": 42.5, "items": []},
    })
    result = _client().get_holders("addr")
    assert result["holder"] == 1000
    assert result["top10_hold_percent"] == 42.5
