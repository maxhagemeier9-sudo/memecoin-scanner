"""Tests für einen einzelnen Monitor-Scan-Zyklus - gemockt, kein Netzwerk,
kein echter Sleep/Loop."""
from unittest.mock import patch

from history import connect
from monitor import scan_once
from risk import build_report
from risk_scan import TokenAssessment
from score import Score


def _result(symbol, address, score_total):
    report = build_report(address, symbol, [], [])
    score = Score(total=score_total, capped=False, breakdown=[])
    listing = {"address": address, "symbol": symbol, "liquidityAddedAt": "2026-09-09T00:00:00"}
    return listing, TokenAssessment(report=report, score=score, liquidity_usd=1_000)


@patch("monitor.alert")
@patch("monitor.scan_new_coins")
def test_coin_above_threshold_triggers_alert(mock_scan, mock_alert):
    mock_scan.return_value = [_result("HOT", "addr1", 85)]
    conn = connect(db_path=":memory:")
    try:
        scan_once(client=object(), conn=conn, alerted_addresses=set())
        mock_alert.assert_called_once()
        assert "HOT" in mock_alert.call_args[0][0]
    finally:
        conn.close()


@patch("monitor.alert")
@patch("monitor.scan_new_coins")
def test_coin_below_threshold_does_not_alert(mock_scan, mock_alert):
    mock_scan.return_value = [_result("MEH", "addr1", 30)]
    conn = connect(db_path=":memory:")
    try:
        scan_once(client=object(), conn=conn, alerted_addresses=set())
        mock_alert.assert_not_called()
    finally:
        conn.close()


@patch("monitor.alert")
@patch("monitor.scan_new_coins")
def test_already_alerted_address_is_not_alerted_again(mock_scan, mock_alert):
    mock_scan.return_value = [_result("HOT", "addr1", 85)]
    conn = connect(db_path=":memory:")
    try:
        alerted = {"addr1"}
        scan_once(client=object(), conn=conn, alerted_addresses=alerted)
        mock_alert.assert_not_called()
    finally:
        conn.close()


@patch("monitor.alert")
@patch("monitor.scan_new_coins")
def test_alerted_address_is_added_to_set(mock_scan, mock_alert):
    mock_scan.return_value = [_result("HOT", "addr1", 85)]
    conn = connect(db_path=":memory:")
    try:
        alerted: set[str] = set()
        scan_once(client=object(), conn=conn, alerted_addresses=alerted)
        assert "addr1" in alerted
    finally:
        conn.close()
