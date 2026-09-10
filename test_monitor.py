"""Tests für einen einzelnen Monitor-Scan-Zyklus - gemockt, kein Netzwerk,
kein echter Sleep/Loop, kein echter Telegram-Versand."""
from unittest.mock import patch

from history import connect, has_been_alerted, mark_alerted
from monitor import scan_once
from risk import RiskFinding, Severity, build_report
from risk_scan import TokenAssessment
from score import Score
from telegram_alerts import TelegramError


def _result(symbol, address, score_total, findings=None):
    findings = findings or []
    report = build_report(address, symbol, findings, [])
    score = Score(total=score_total, capped=False, breakdown=[])
    listing = {"address": address, "symbol": symbol, "liquidityAddedAt": "2026-09-09T00:00:00"}
    return listing, TokenAssessment(report=report, score=score, liquidity_usd=1_000)


@patch("monitor.send_telegram_message")
@patch("monitor.alert")
@patch("monitor.scan_new_coins")
def test_high_score_triggers_alert(mock_scan, mock_alert, mock_send):
    mock_scan.return_value = [_result("HOT", "addr1", 85)]
    conn = connect(db_path=":memory:")
    try:
        scan_once(client=object(), conn=conn)
        mock_alert.assert_called_once()
        assert "HOT" in mock_alert.call_args[0][0]
    finally:
        conn.close()


@patch("monitor.send_telegram_message")
@patch("monitor.alert")
@patch("monitor.scan_new_coins")
def test_low_score_and_high_risk_does_not_alert(mock_scan, mock_alert, mock_send):
    findings = [RiskFinding(Severity.HOCH, "Sehr geringe Liquidität")]
    mock_scan.return_value = [_result("MEH", "addr1", 30, findings=findings)]
    conn = connect(db_path=":memory:")
    try:
        scan_once(client=object(), conn=conn)
        mock_alert.assert_not_called()
    finally:
        conn.close()


@patch("monitor.send_telegram_message")
@patch("monitor.alert")
@patch("monitor.scan_new_coins")
def test_kritisch_risk_with_capped_low_score_does_not_alert(mock_scan, mock_alert, mock_send):
    findings = [RiskFinding(Severity.KRITISCH, "Mint-Authority aktiv")]
    mock_scan.return_value = [_result("BAD", "addr1", 10, findings=findings)]
    conn = connect(db_path=":memory:")
    try:
        scan_once(client=object(), conn=conn)
        mock_alert.assert_not_called()
    finally:
        conn.close()


@patch("monitor.send_telegram_message")
@patch("monitor.alert")
@patch("monitor.scan_new_coins")
def test_mittel_risk_alerts_even_with_low_score(mock_scan, mock_alert, mock_send):
    findings = [RiskFinding(Severity.MITTEL, "Nur 40 Holder insgesamt")]
    mock_scan.return_value = [_result("MID", "addr1", 45, findings=findings)]  # Score < 70
    conn = connect(db_path=":memory:")
    try:
        scan_once(client=object(), conn=conn)
        mock_alert.assert_called_once()
    finally:
        conn.close()


@patch("monitor.send_telegram_message")
@patch("monitor.alert")
@patch("monitor.scan_new_coins")
def test_niedrig_risk_alerts_even_with_low_score(mock_scan, mock_alert, mock_send):
    mock_scan.return_value = [_result("SAFE", "addr1", 20)]  # keine Findings -> NIEDRIG
    conn = connect(db_path=":memory:")
    try:
        scan_once(client=object(), conn=conn)
        mock_alert.assert_called_once()
    finally:
        conn.close()


@patch("monitor.send_telegram_message")
@patch("monitor.alert")
@patch("monitor.scan_new_coins")
def test_already_alerted_address_is_not_alerted_again(mock_scan, mock_alert, mock_send):
    mock_scan.return_value = [_result("HOT", "addr1", 85)]
    conn = connect(db_path=":memory:")
    try:
        mark_alerted(conn, "addr1")
        scan_once(client=object(), conn=conn)
        mock_alert.assert_not_called()
    finally:
        conn.close()


@patch("monitor.send_telegram_message")
@patch("monitor.alert")
@patch("monitor.scan_new_coins")
def test_alerted_address_is_persisted_in_history_db(mock_scan, mock_alert, mock_send):
    mock_scan.return_value = [_result("HOT", "addr1", 85)]
    conn = connect(db_path=":memory:")
    try:
        scan_once(client=object(), conn=conn)
        assert has_been_alerted(conn, "addr1")
    finally:
        conn.close()


@patch("monitor.send_telegram_message")
@patch("monitor.alert")
@patch("monitor.scan_new_coins")
def test_alert_dedup_survives_a_fresh_connection_to_the_same_db_file(mock_scan, mock_alert, mock_send, tmp_path):
    db_path = tmp_path / "history.sqlite3"
    mock_scan.return_value = [_result("HOT", "addr1", 85)]

    conn1 = connect(db_path=str(db_path))
    scan_once(client=object(), conn=conn1)
    conn1.close()

    # neue Verbindung, wie bei einem frischen Prozess-Start (z.B. GitHub Actions)
    conn2 = connect(db_path=str(db_path))
    try:
        scan_once(client=object(), conn=conn2)
        assert mock_alert.call_count == 1  # nicht beim zweiten Mal erneut alarmiert
    finally:
        conn2.close()


@patch("monitor.send_telegram_message")
@patch("monitor.alert")
@patch("monitor.scan_new_coins")
def test_every_scan_sends_a_telegram_summary_regardless_of_threshold(mock_scan, mock_alert, mock_send):
    mock_scan.return_value = [_result("MEH", "addr1", 20)]  # weit unter der Alert-Schwelle
    conn = connect(db_path=":memory:")
    try:
        scan_once(client=object(), conn=conn)
        mock_send.assert_called_once()
        text, kwargs = mock_send.call_args[0][0], mock_send.call_args[1]
        assert "MEH" in text
        assert "addr1" in text
        assert kwargs.get("parse_mode") == "HTML"
    finally:
        conn.close()


@patch("monitor.send_telegram_message", side_effect=TelegramError("boom"))
@patch("monitor.alert")
@patch("monitor.scan_new_coins")
def test_telegram_summary_failure_does_not_crash_scan(mock_scan, mock_alert, mock_send):
    mock_scan.return_value = [_result("MEH", "addr1", 20)]
    conn = connect(db_path=":memory:")
    try:
        scan_once(client=object(), conn=conn)  # darf nicht crashen
    finally:
        conn.close()
