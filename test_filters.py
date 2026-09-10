"""Tests für die Filterlogik - laufen offline, ohne Birdeye-API."""
from config import FilterCriteria
from filters import evaluate_token, evaluate_tokens

CRITERIA = FilterCriteria(min_market_cap=50_000, max_market_cap=50_000_000, min_volume_24h=100_000)


def _token(symbol="ABC", name="Abc Coin", mc=1_000_000, v24hUSD=500_000):
    return {"symbol": symbol, "name": name, "mc": mc, "v24hUSD": v24hUSD}


def test_token_passing_all_criteria_is_accepted():
    result = evaluate_token(_token(), CRITERIA)
    assert result.passed
    assert result.reason == ""


def test_market_cap_too_low_is_rejected():
    result = evaluate_token(_token(mc=10_000), CRITERIA)
    assert not result.passed
    assert "niedrig" in result.reason


def test_market_cap_too_high_is_rejected():
    result = evaluate_token(_token(mc=100_000_000), CRITERIA)
    assert not result.passed
    assert "hoch" in result.reason


def test_volume_too_low_is_rejected():
    result = evaluate_token(_token(v24hUSD=1_000), CRITERIA)
    assert not result.passed
    assert "Volumen" in result.reason


def test_missing_fields_default_to_zero_and_get_rejected():
    result = evaluate_token({"symbol": "XYZ"}, CRITERIA)
    assert not result.passed
    assert result.market_cap == 0
    assert result.volume_24h == 0


def test_evaluate_tokens_preserves_order_and_count():
    tokens = [_token(symbol="A"), _token(symbol="B", mc=1_000)]
    results = evaluate_tokens(tokens, CRITERIA)
    assert [r.symbol for r in results] == ["A", "B"]
    assert results[0].passed
    assert not results[1].passed
