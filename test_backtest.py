"""Tests für die Backtesting-Engine - gemockt, kein echter OHLCV-Abruf."""
from unittest.mock import MagicMock

from backtest import backtest_snapshot, run_backtest, summarize_by_risk_level
from birdeye_client import BirdeyeAPIError
from history import Snapshot, connect, record_snapshot


def _snapshot(address="addr1", price=1.0, risk_level="MITTEL", scanned_at="2026-09-09T00:00:00+00:00", **overrides):
    params = dict(
        address=address, symbol="SYM", scanned_at=scanned_at, liquidity_usd=10_000.0,
        volume_24h_usd=5_000.0, holder_count=100, top10_percent=40.0, score_total=60,
        risk_level=risk_level, creator_authority=None, price=price,
    )
    params.update(overrides)
    return Snapshot(**params)


def _client_with_candles(candles):
    client = MagicMock()
    client.get_ohlcv.return_value = candles
    return client


def test_backtest_snapshot_computes_positive_return():
    snapshot = _snapshot(price=1.0)
    client = _client_with_candles([{"c": 1.5, "unix_time": 1_757_376_600}])
    result = backtest_snapshot(client, snapshot, horizon_minutes=60)
    assert result is not None
    assert result.return_percent == 50.0
    assert result.entry_price == 1.0
    assert result.exit_price == 1.5


def test_backtest_snapshot_computes_negative_return():
    snapshot = _snapshot(price=2.0)
    client = _client_with_candles([{"c": 0.5, "unix_time": 1_757_376_600}])
    result = backtest_snapshot(client, snapshot, horizon_minutes=60)
    assert result.return_percent == -75.0


def test_backtest_snapshot_returns_none_without_entry_price():
    snapshot = _snapshot(price=None)
    result = backtest_snapshot(_client_with_candles([]), snapshot)
    assert result is None


def test_backtest_snapshot_returns_none_when_no_candles():
    snapshot = _snapshot(price=1.0)
    result = backtest_snapshot(_client_with_candles([]), snapshot)
    assert result is None


def test_backtest_snapshot_returns_none_on_api_error():
    snapshot = _snapshot(price=1.0)
    client = MagicMock()
    client.get_ohlcv.side_effect = BirdeyeAPIError("rate limited")
    result = backtest_snapshot(client, snapshot)
    assert result is None


def test_backtest_snapshot_uses_last_candle_as_exit():
    snapshot = _snapshot(price=1.0)
    client = _client_with_candles([
        {"c": 1.2, "unix_time": 1_757_376_000},
        {"c": 1.8, "unix_time": 1_757_376_600},  # letzte Kerze zaehlt
    ])
    result = backtest_snapshot(client, snapshot)
    assert result.exit_price == 1.8


def test_run_backtest_skips_snapshots_without_price():
    conn = connect(db_path=":memory:")
    try:
        record_snapshot(conn, _snapshot(address="a1", price=None))
        record_snapshot(conn, _snapshot(address="a2", price=1.0))

        client = _client_with_candles([{"c": 2.0, "unix_time": 1_757_376_600}])
        results = run_backtest(client, conn)

        assert len(results) == 1
        assert results[0].address == "a2"
    finally:
        conn.close()


def test_summarize_by_risk_level_groups_correctly():
    from backtest import BacktestResult

    results = [
        BacktestResult("a1", "S1", "t", 80, "NIEDRIG", 1.0, 2.0, 100.0, 60),
        BacktestResult("a2", "S2", "t", 30, "HOCH", 1.0, 0.5, -50.0, 60),
        BacktestResult("a3", "S3", "t", 35, "HOCH", 1.0, 0.8, -20.0, 60),
    ]
    summary = summarize_by_risk_level(results)

    assert summary["NIEDRIG"]["count"] == 1
    assert summary["NIEDRIG"]["avg_return_percent"] == 100.0
    assert summary["HOCH"]["count"] == 2
    assert summary["HOCH"]["avg_return_percent"] == -35.0
