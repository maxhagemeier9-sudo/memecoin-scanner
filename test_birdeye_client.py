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


@patch("birdeye_client.requests.Session.get")
def test_get_ohlcv_returns_items_list(mock_get):
    mock_get.return_value = _response(200, {
        "data": {"items": [{"o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "unix_time": 1700000000}]},
    })
    result = _client().get_ohlcv("addr", type_="1H", time_from=1700000000, time_to=1700100000)
    assert result == [{"o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "unix_time": 1700000000}]

    params = mock_get.call_args[1]["params"]
    assert params["time_from"] == 1700000000
    assert params["time_to"] == 1700100000


@patch("birdeye_client.requests.Session.get")
def test_get_ohlcv_omits_time_params_when_not_given(mock_get):
    mock_get.return_value = _response(200, {"data": {"items": []}})
    _client().get_ohlcv("addr")
    params = mock_get.call_args[1]["params"]
    assert "time_from" not in params
    assert "time_to" not in params


@patch("birdeye_client.requests.Session.get")
def test_get_top_traders_returns_items_list(mock_get):
    mock_get.return_value = _response(200, {
        "data": {"items": [{"owner": "wallet1", "volume": 500.0}]},
    })
    result = _client().get_top_traders("addr")
    assert result == [{"owner": "wallet1", "volume": 500.0}]
