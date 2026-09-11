"""Tests für die Backtesting-Engine - gemockt, kein echter OHLCV-Abruf."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

from backtest import (
    backtest_snapshot,
    maybe_run_daily_backtest_batch,
    run_backtest,
    run_daily_backtest_batch,
    stored_results,
    summarize_by_risk_level,
)
from geckoterminal_client import GeckoTerminalAPIError
from history import Snapshot, connect, record_snapshot


def _snapshot(
    address="addr1", price=1.0, risk_level="MITTEL", scanned_at="2026-09-09T00:00:00+00:00",
    pool_address="pool1", **overrides,
):
    params = dict(
        address=address, symbol="SYM", scanned_at=scanned_at, liquidity_usd=10_000.0,
        volume_24h_usd=5_000.0, holder_count=None, top10_percent=40.0, score_total=60,
        risk_level=risk_level, creator_authority=None, price=price, pool_address=pool_address,
    )
    params.update(overrides)
    return Snapshot(**params)


def _client_with_candles(candles):
    client = MagicMock()
    client.get_ohlcv.return_value = candles
    return client


# GeckoTerminal liefert Kerzen NEUESTE ZUERST: [unix_time, open, high, low, close, volume]
def _candle(unix_time, close):
    return [unix_time, close, close, close, close, 100.0]


def test_backtest_snapshot_computes_positive_return():
    snapshot = _snapshot(price=1.0)
    client = _client_with_candles([_candle(1_757_376_600, 1.5)])
    result = backtest_snapshot(client, snapshot, horizon_minutes=60)
    assert result is not None
    assert result.return_percent == 50.0
    assert result.entry_price == 1.0
    assert result.exit_price == 1.5


def test_backtest_snapshot_computes_negative_return():
    snapshot = _snapshot(price=2.0)
    client = _client_with_candles([_candle(1_757_376_600, 0.5)])
    result = backtest_snapshot(client, snapshot, horizon_minutes=60)
    assert result.return_percent == -75.0


def test_backtest_snapshot_returns_none_without_entry_price():
    snapshot = _snapshot(price=None)
    result = backtest_snapshot(_client_with_candles([]), snapshot)
    assert result is None


def test_backtest_snapshot_returns_none_without_pool_address():
    snapshot = _snapshot(pool_address=None)
    result = backtest_snapshot(_client_with_candles([_candle(1_757_376_600, 1.5)]), snapshot)
    assert result is None


def test_backtest_snapshot_returns_none_when_no_candles():
    snapshot = _snapshot(price=1.0)
    result = backtest_snapshot(_client_with_candles([]), snapshot)
    assert result is None


def test_backtest_snapshot_returns_none_on_api_error():
    snapshot = _snapshot(price=1.0)
    client = MagicMock()
    client.get_ohlcv.side_effect = GeckoTerminalAPIError("rate limited")
    result = backtest_snapshot(client, snapshot)
    assert result is None


def test_backtest_snapshot_uses_first_candle_as_exit_since_newest_first():
    snapshot = _snapshot(price=1.0)
    client = _client_with_candles([
        _candle(1_757_376_600, 1.8),  # neueste Kerze steht zuerst
        _candle(1_757_376_000, 1.2),
    ])
    result = backtest_snapshot(client, snapshot)
    assert result.exit_price == 1.8


def test_run_backtest_skips_snapshots_without_price():
    conn = connect(db_path=":memory:")
    try:
        record_snapshot(conn, _snapshot(address="a1", price=None))
        record_snapshot(conn, _snapshot(address="a2", price=1.0))

        client = _client_with_candles([_candle(1_757_376_600, 2.0)])
        results = run_backtest(client, conn)

        assert len(results) == 1
        assert results[0].address == "a2"
    finally:
        conn.close()


def test_run_backtest_skips_snapshots_without_pool_address():
    conn = connect(db_path=":memory:")
    try:
        record_snapshot(conn, _snapshot(address="a1", price=1.0, pool_address=None))
        client = _client_with_candles([_candle(1_757_376_600, 2.0)])
        results = run_backtest(client, conn)
        assert results == []
    finally:
        conn.close()


def test_run_daily_backtest_batch_skips_snapshots_younger_than_horizon():
    conn = connect(db_path=":memory:")
    try:
        now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
        record_snapshot(conn, _snapshot(address="a1", scanned_at="2026-09-11T11:50:00+00:00"))  # nur 10 Min alt

        client = _client_with_candles([_candle(1_757_376_600, 2.0)])
        results = run_daily_backtest_batch(client, conn, horizon_minutes=60, now=now)

        assert results == []
    finally:
        conn.close()


def test_run_daily_backtest_batch_tests_mature_snapshots_and_persists_them():
    conn = connect(db_path=":memory:")
    try:
        now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
        record_snapshot(conn, _snapshot(address="a1", scanned_at="2026-09-11T10:00:00+00:00"))  # 2h alt

        client = _client_with_candles([_candle(1_757_376_600, 2.0)])
        results = run_daily_backtest_batch(client, conn, horizon_minutes=60, now=now)

        assert len(results) == 1
        assert results[0].address == "a1"
        assert len(stored_results(conn)) == 1
    finally:
        conn.close()


def test_run_daily_backtest_batch_never_retests_an_already_backtested_address():
    conn = connect(db_path=":memory:")
    try:
        now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
        record_snapshot(conn, _snapshot(address="a1", scanned_at="2026-09-11T10:00:00+00:00"))

        client = _client_with_candles([_candle(1_757_376_600, 2.0)])
        run_daily_backtest_batch(client, conn, horizon_minutes=60, now=now)
        second_run = run_daily_backtest_batch(client, conn, horizon_minutes=60, now=now)

        assert second_run == []
        assert len(stored_results(conn)) == 1
    finally:
        conn.close()


def test_run_daily_backtest_batch_respects_the_limit():
    conn = connect(db_path=":memory:")
    try:
        now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
        for i in range(5):
            record_snapshot(conn, _snapshot(address=f"a{i}", scanned_at="2026-09-11T10:00:00+00:00"))

        client = _client_with_candles([_candle(1_757_376_600, 2.0)])
        results = run_daily_backtest_batch(client, conn, horizon_minutes=60, limit=2, now=now)

        assert len(results) == 2
    finally:
        conn.close()


def test_maybe_run_daily_backtest_batch_only_runs_in_its_hour_window():
    conn = connect(db_path=":memory:")
    try:
        outside_window = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
        record_snapshot(conn, _snapshot(address="a1", scanned_at="2026-09-11T10:00:00+00:00"))

        client = _client_with_candles([_candle(1_757_376_600, 2.0)])
        results = maybe_run_daily_backtest_batch(client, conn, now=outside_window)

        assert results == []
        assert stored_results(conn) == []
    finally:
        conn.close()


def test_maybe_run_daily_backtest_batch_runs_once_per_day():
    conn = connect(db_path=":memory:")
    try:
        in_window = datetime(2026, 9, 11, 9, 5, tzinfo=timezone.utc)
        record_snapshot(conn, _snapshot(address="a1", scanned_at="2026-09-11T07:00:00+00:00"))
        record_snapshot(conn, _snapshot(address="a2", scanned_at="2026-09-11T07:00:00+00:00"))

        client = _client_with_candles([_candle(1_757_376_600, 2.0)])
        first_run = maybe_run_daily_backtest_batch(client, conn, now=in_window)
        second_run = maybe_run_daily_backtest_batch(client, conn, now=in_window)

        assert len(first_run) == 2
        assert second_run == []  # bereits heute gelaufen, obwohl a2 evtl. noch offen waere
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
