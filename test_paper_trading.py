"""Tests für Paper Trading - gemockt/offline, keine echten API-Calls."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from geckoterminal_client import GeckoTerminalAPIError
from history import connect
from paper_trading import (
    PaperTrade,
    close_trade,
    closed_trades,
    decide_exit,
    ensure_schema,
    has_open_trade,
    open_trade,
    open_trades,
    process_open_trades,
    summarize,
)


@pytest.fixture
def conn():
    connection = connect(db_path=":memory:")
    ensure_schema(connection)
    yield connection
    connection.close()


def _trade(entry_price=1.0, entry_at=None, **overrides):
    return PaperTrade(
        id=1, address="addr1", symbol="SYM",
        entry_at=entry_at or datetime.now(timezone.utc).isoformat(),
        entry_price=entry_price, entry_score=60, entry_risk_level="MITTEL",
        status="OPEN",
    )


def test_open_trade_then_has_open_trade_is_true(conn):
    open_trade(conn, "addr1", "SYM", entry_price=1.0, entry_score=60, entry_risk_level="MITTEL")
    assert has_open_trade(conn, "addr1") is True


def test_has_open_trade_is_false_for_unknown_address(conn):
    assert has_open_trade(conn, "unknown") is False


def test_open_trades_lists_only_open_positions(conn):
    open_trade(conn, "addr1", "SYM1", 1.0, 60, "MITTEL")
    trades = open_trades(conn)
    assert len(trades) == 1
    assert trades[0].address == "addr1"
    assert trades[0].status == "OPEN"


def test_close_trade_computes_pnl_correctly(conn):
    open_trade(conn, "addr1", "SYM", entry_price=1.0, entry_score=60, entry_risk_level="MITTEL")
    trade = open_trades(conn)[0]

    close_trade(conn, trade.id, exit_price=2.0, exit_reason="TAKE_PROFIT", trade_size_usd=100.0)

    closed = closed_trades(conn)
    assert len(closed) == 1
    assert closed[0].pnl_percent == 100.0
    assert closed[0].pnl_usd == 100.0
    assert closed[0].status == "CLOSED"
    assert closed[0].exit_reason == "TAKE_PROFIT"


def test_closed_trade_no_longer_counts_as_open(conn):
    open_trade(conn, "addr1", "SYM", 1.0, 60, "MITTEL")
    trade = open_trades(conn)[0]
    close_trade(conn, trade.id, exit_price=1.5, exit_reason="TAKE_PROFIT")

    assert has_open_trade(conn, "addr1") is False
    assert open_trades(conn) == []


def test_decide_exit_take_profit():
    trade = _trade(entry_price=1.0)
    assert decide_exit(trade, current_price=2.0) == "TAKE_PROFIT"  # +100%


def test_decide_exit_stop_loss():
    trade = _trade(entry_price=1.0)
    assert decide_exit(trade, current_price=0.4) == "STOP_LOSS"  # -60%


def test_decide_exit_max_hold_time():
    entry_at = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    trade = _trade(entry_price=1.0, entry_at=entry_at)
    assert decide_exit(trade, current_price=1.1) == "MAX_HOLD"


def test_decide_exit_none_when_position_should_stay_open():
    trade = _trade(entry_price=1.0, entry_at=datetime.now(timezone.utc).isoformat())
    assert decide_exit(trade, current_price=1.1) is None


def test_process_open_trades_closes_on_take_profit(conn):
    open_trade(conn, "addr1", "SYM", entry_price=1.0, entry_score=60, entry_risk_level="MITTEL")
    client = MagicMock()
    client.get_token_price.return_value = 3.0  # +200%

    closed = process_open_trades(client, conn)

    assert len(closed) == 1
    trade, reason, exit_price = closed[0]
    assert reason == "TAKE_PROFIT"
    assert exit_price == 3.0
    assert has_open_trade(conn, "addr1") is False


def test_process_open_trades_leaves_position_open_when_no_exit_rule_triggers(conn):
    open_trade(conn, "addr1", "SYM", entry_price=1.0, entry_score=60, entry_risk_level="MITTEL")
    client = MagicMock()
    client.get_token_price.return_value = 1.05

    closed = process_open_trades(client, conn)

    assert closed == []
    assert has_open_trade(conn, "addr1") is True


def test_process_open_trades_survives_price_lookup_failure(conn):
    open_trade(conn, "addr1", "SYM", entry_price=1.0, entry_score=60, entry_risk_level="MITTEL")
    client = MagicMock()
    client.get_token_price.side_effect = GeckoTerminalAPIError("rate limited")

    closed = process_open_trades(client, conn)  # darf nicht crashen

    assert closed == []
    assert has_open_trade(conn, "addr1") is True


def test_process_open_trades_skips_when_price_is_none(conn):
    open_trade(conn, "addr1", "SYM", entry_price=1.0, entry_score=60, entry_risk_level="MITTEL")
    client = MagicMock()
    client.get_token_price.return_value = None

    closed = process_open_trades(client, conn)

    assert closed == []
    assert has_open_trade(conn, "addr1") is True


def test_summarize_empty_list():
    result = summarize([])
    assert result["count"] == 0
    assert result["total_pnl_usd"] == 0.0


def test_summarize_computes_win_rate_and_totals():
    trades = [
        PaperTrade(1, "a1", "S1", "t", 1.0, 50, "MITTEL", "CLOSED", pnl_percent=50.0, pnl_usd=50.0),
        PaperTrade(2, "a2", "S2", "t", 1.0, 50, "MITTEL", "CLOSED", pnl_percent=-20.0, pnl_usd=-20.0),
    ]
    result = summarize(trades)
    assert result["count"] == 2
    assert result["win_rate_percent"] == 50.0
    assert result["total_pnl_usd"] == 30.0
    assert result["avg_pnl_percent"] == 15.0
