"""Tests für GeckoTerminalClient und parse_pool - gemockt, keine Live-Calls."""
from unittest.mock import Mock, patch

import pytest

from geckoterminal_client import GeckoTerminalAPIError, GeckoTerminalClient, parse_pool


def _response(status_code, json_data=None, text=""):
    resp = Mock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text
    return resp


REAL_POOL = {
    "attributes": {
        "address": "GLLTUe1nEPKFXFwsh6qU7p1Wc43PmRQqhwoH6DH9SuV4",
        "name": "SHRINE / SOL",
        "pool_created_at": "2026-09-10T17:07:36Z",
        "base_token_price_usd": "0.00000442446834980199560068713358859434623339124703307733735606233825",
        "reserve_in_usd": None,
        "volume_usd": {"h24": "49.2168648745"},
        "transactions": {"h24": {"buys": 5, "sells": 3, "buyers": 4, "sellers": 2}},
    },
    "relationships": {
        "base_token": {"data": {"id": "solana_56aQ4H6LhbTHLTM9YLBYHtAGnEBBurbEuu8iGqQxpump", "type": "token"}},
        "quote_token": {"data": {"id": "solana_So11111111111111111111111111111111111111112", "type": "token"}},
        "dex": {"data": {"id": "pump-fun", "type": "dex"}},
    },
}


@patch("geckoterminal_client.requests.Session.get")
def test_get_new_pools_returns_data_list(mock_get):
    mock_get.return_value = _response(200, {"data": [REAL_POOL]})
    result = GeckoTerminalClient().get_new_pools(page=1)
    assert result == [REAL_POOL]
    params = mock_get.call_args[1]["params"]
    assert params["page"] == 1


@patch("geckoterminal_client.requests.Session.get")
def test_get_token_price_returns_float(mock_get):
    mock_get.return_value = _response(200, {"data": {"attributes": {"price_usd": "99.365"}}})
    result = GeckoTerminalClient().get_token_price("addr")
    assert result == pytest.approx(99.365)


@patch("geckoterminal_client.requests.Session.get")
def test_get_token_price_returns_none_when_missing(mock_get):
    mock_get.return_value = _response(200, {"data": {"attributes": {}}})
    result = GeckoTerminalClient().get_token_price("addr")
    assert result is None


@patch("geckoterminal_client.requests.Session.get")
def test_get_ohlcv_returns_ohlcv_list(mock_get):
    mock_get.return_value = _response(200, {"data": {"attributes": {"ohlcv_list": [[1700000000, 1, 2, 0.5, 1.5, 100]]}}})
    result = GeckoTerminalClient().get_ohlcv("pool-addr")
    assert result == [[1700000000, 1, 2, 0.5, 1.5, 100]]


@patch("geckoterminal_client.time.sleep", return_value=None)
@patch("geckoterminal_client.requests.Session.get")
def test_429_is_retried_and_succeeds(mock_get, mock_sleep):
    mock_get.side_effect = [_response(429), _response(200, {"data": []})]
    result = GeckoTerminalClient().get_new_pools()
    assert result == []
    assert mock_get.call_count == 2


@patch("geckoterminal_client.requests.Session.get")
def test_non_retryable_error_raises_immediately(mock_get):
    mock_get.return_value = _response(401, text="unauthorized")
    with pytest.raises(GeckoTerminalAPIError):
        GeckoTerminalClient().get_new_pools()
    assert mock_get.call_count == 1


def test_parse_pool_extracts_token_address_without_network_prefix():
    listing = parse_pool(REAL_POOL)
    assert listing.token_address == "56aQ4H6LhbTHLTM9YLBYHtAGnEBBurbEuu8iGqQxpump"
    assert listing.pool_address == "GLLTUe1nEPKFXFwsh6qU7p1Wc43PmRQqhwoH6DH9SuV4"


def test_parse_pool_extracts_symbol_from_pool_name():
    listing = parse_pool(REAL_POOL)
    assert listing.symbol == "SHRINE"


def test_parse_pool_computes_trade_and_wallet_counts_from_h24():
    listing = parse_pool(REAL_POOL)
    assert listing.trade_24h == 8  # 5 buys + 3 sells
    assert listing.unique_wallet_24h == 6  # 4 buyers + 2 sellers


def test_parse_pool_handles_null_liquidity_gracefully():
    listing = parse_pool(REAL_POOL)
    assert listing.liquidity_usd is None


def test_parse_pool_parses_price_and_volume():
    listing = parse_pool(REAL_POOL)
    assert listing.price_usd == pytest.approx(0.00000442446834980199560068713358859434623339124703307733735606233825)
    assert listing.volume_24h_usd == pytest.approx(49.2168648745)


def test_parse_pool_returns_none_when_base_token_missing():
    broken = {"attributes": {}, "relationships": {}}
    assert parse_pool(broken) is None


def test_parse_pool_extracts_locked_liquidity_percentage_when_present():
    pool_with_lock = {
        "attributes": {**REAL_POOL["attributes"], "locked_liquidity_percentage": "97.5"},
        "relationships": REAL_POOL["relationships"],
    }
    listing = parse_pool(pool_with_lock)
    assert listing.locked_liquidity_percentage == pytest.approx(97.5)


def test_parse_pool_locked_liquidity_percentage_is_none_when_absent():
    # new_pools-Liste liefert dieses Feld nicht, nur die Pool-Detail-Antwort
    listing = parse_pool(REAL_POOL)
    assert listing.locked_liquidity_percentage is None


def test_parse_pool_falls_back_to_placeholder_symbol():
    pool = {
        "attributes": {"address": "a", "name": "", "transactions": {}, "volume_usd": {}},
        "relationships": {"base_token": {"data": {"id": "solana_abc"}}},
    }
    listing = parse_pool(pool)
    assert listing.symbol == "???"
