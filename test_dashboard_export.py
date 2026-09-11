"""Tests für dashboard_export.py - reine DB-Lesefunktionen, keine echten
API-Calls, laufen komplett offline auf einer In-Memory-SQLite-DB."""
import pytest

from dashboard_export import build_dashboard_data
from history import Snapshot, connect, mark_alerted, mark_rising_alerted, record_snapshot
from paper_trading import close_trade, ensure_schema, open_trade, open_trades


@pytest.fixture
def conn():
    connection = connect(db_path=":memory:")
    ensure_schema(connection)
    yield connection
    connection.close()


def _snapshot(address="addr1", symbol="SYM", scanned_at="2026-09-11T10:00:00+00:00", **overrides):
    defaults = dict(
        address=address, symbol=symbol, scanned_at=scanned_at,
        liquidity_usd=10_000.0, volume_24h_usd=5_000.0, holder_count=None,
        top10_percent=40.0, score_total=60, risk_level="MITTEL",
        pool_address="pool1",
    )
    defaults.update(overrides)
    return Snapshot(**defaults)


def test_build_dashboard_data_includes_generated_at_and_stats(conn):
    record_snapshot(conn, _snapshot())
    data = build_dashboard_data(conn)

    assert "generated_at" in data
    assert data["stats"]["total_snapshots"] == 1
    assert data["stats"]["total_unique_coins"] == 1


def test_recent_snapshots_are_ordered_newest_first(conn):
    record_snapshot(conn, _snapshot(address="a1", symbol="OLD", scanned_at="2026-09-11T09:00:00+00:00"))
    record_snapshot(conn, _snapshot(address="a2", symbol="NEW", scanned_at="2026-09-11T10:00:00+00:00"))

    data = build_dashboard_data(conn)

    symbols = [s["symbol"] for s in data["recent_snapshots"]]
    assert symbols[0] == "NEW"
    assert symbols[1] == "OLD"


def test_recent_snapshot_includes_chart_url_from_pool_address(conn):
    record_snapshot(conn, _snapshot(pool_address="MyPool123"))
    data = build_dashboard_data(conn)
    assert data["recent_snapshots"][0]["chart_url"] == "https://www.geckoterminal.com/solana/pools/MyPool123"


def test_recent_alerts_include_both_regular_and_rising(conn):
    record_snapshot(conn, _snapshot(address="a1", symbol="HOT"))
    record_snapshot(conn, _snapshot(address="a2", symbol="RISER"))
    mark_alerted(conn, "a1")
    mark_rising_alerted(conn, "a2")

    data = build_dashboard_data(conn)

    kinds = {a["address"]: a["kind"] for a in data["recent_alerts"]}
    assert kinds["a1"] == "alert"
    assert kinds["a2"] == "rising"


def test_recent_alert_carries_symbol_and_score_from_latest_snapshot(conn):
    record_snapshot(conn, _snapshot(address="a1", symbol="HOT", score_total=85))
    mark_alerted(conn, "a1")

    data = build_dashboard_data(conn)

    assert data["recent_alerts"][0]["symbol"] == "HOT"
    assert data["recent_alerts"][0]["score"] == 85


def test_stats_risk_level_counts_reflect_latest_snapshot_per_address(conn):
    # zwei Snapshots derselben Adresse - nur der LETZTE Stand zaehlt
    record_snapshot(conn, _snapshot(address="a1", risk_level="HOCH", scanned_at="2026-09-11T09:00:00+00:00"))
    record_snapshot(conn, _snapshot(address="a1", risk_level="NIEDRIG", scanned_at="2026-09-11T10:00:00+00:00"))

    data = build_dashboard_data(conn)

    assert data["stats"]["risk_level_counts"] == {"NIEDRIG": 1}


def test_paper_trading_section_includes_open_and_closed_positions(conn):
    open_trade(conn, "addr1", "HOT", entry_price=1.0, entry_score=70, entry_risk_level="NIEDRIG")
    open_trade(conn, "addr2", "COLD", entry_price=2.0, entry_score=50, entry_risk_level="MITTEL")
    trade = open_trades(conn)[0]
    close_trade(conn, trade.id, exit_price=1.5, exit_reason="TAKE_PROFIT", trade_size_usd=100.0)

    data = build_dashboard_data(conn)

    assert len(data["paper_trading"]["open_positions"]) == 1
    assert len(data["paper_trading"]["closed_trades"]) == 1
    assert data["paper_trading"]["closed_trades"][0]["exit_reason"] == "TAKE_PROFIT"
    assert data["paper_trading"]["summary"]["count"] == 1


def test_empty_database_produces_valid_empty_structure(conn):
    data = build_dashboard_data(conn)

    assert data["recent_snapshots"] == []
    assert data["recent_alerts"] == []
    assert data["paper_trading"]["open_positions"] == []
    assert data["paper_trading"]["closed_trades"] == []
    assert data["stats"]["total_snapshots"] == 0
