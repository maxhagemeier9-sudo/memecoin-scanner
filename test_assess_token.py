"""Integrationstests für assess_token: prüft die Verdrahtung zwischen
Birdeye-Client, Solana-RPC, Historie und den reinen Bewertungsfunktionen -
alles gemockt, keine Live-Calls.
"""
from unittest.mock import MagicMock, patch

import pytest

from birdeye_client import BirdeyeAPIError
from history import Snapshot, connect, record_snapshot
from risk import Severity
from risk_scan import assess_token
from solana_rpc import MintAuthorities, SolanaRPCError


def _client(holders=None, market_data=None, trade_data=None):
    client = MagicMock()
    client.get_holders.return_value = holders or {"top10_hold_percent": 10.0, "holder": 1000}
    client.get_market_data.return_value = market_data or {"liquidity": 100_000}
    client.get_trade_data.return_value = trade_data or {
        "volume_24h_usd": 200_000, "trade_24h": 100, "unique_wallet_24h": 80,
    }
    return client


@pytest.fixture(autouse=True)
def _no_real_sleep():
    with patch("risk_scan.time.sleep", return_value=None):
        yield


@patch("risk_scan.get_mint_authorities")
def test_healthy_token_produces_high_score_and_no_findings(mock_authorities):
    mock_authorities.return_value = MintAuthorities(mint_authority=None, freeze_authority=None)
    assessment = assess_token(_client(), "addr", "SYM")
    assert assessment.report.overall == Severity.NIEDRIG
    assert assessment.score.total >= 80
    assert not assessment.score.capped


@patch("risk_scan.get_mint_authorities")
def test_active_mint_authority_caps_score(mock_authorities):
    mock_authorities.return_value = MintAuthorities(mint_authority="creator", freeze_authority=None)
    assessment = assess_token(_client(), "addr", "SYM")
    assert assessment.report.overall == Severity.KRITISCH
    assert assessment.score.capped
    assert assessment.score.total <= 10


@patch("risk_scan.get_mint_authorities")
def test_rpc_failure_is_recorded_as_unchecked_without_crashing(mock_authorities):
    mock_authorities.side_effect = SolanaRPCError("timeout")
    assessment = assess_token(_client(), "addr", "SYM")
    assert any("Mint-" in note for note in assessment.report.unchecked)
    # andere Faktoren wurden trotzdem berechnet, obwohl RPC fehlgeschlagen ist
    assert assessment.score.total > 0


@patch("risk_scan.get_mint_authorities")
def test_holder_endpoint_failure_leaves_that_factor_unscored(mock_authorities):
    mock_authorities.return_value = MintAuthorities(mint_authority=None, freeze_authority=None)
    client = _client()
    client.get_holders.side_effect = BirdeyeAPIError("rate limited")

    assessment = assess_token(client, "addr", "SYM")

    assert any("Holder" in note for note in assessment.report.unchecked)
    assert "Holder-Konzentration" in assessment.score.unscored_factors
    assert "Holder-Anzahl" in assessment.score.unscored_factors


@patch("risk_scan.get_mint_authorities")
def test_market_data_failure_leaves_liquidity_usd_none(mock_authorities):
    mock_authorities.return_value = MintAuthorities(mint_authority=None, freeze_authority=None)
    client = _client()
    client.get_market_data.side_effect = BirdeyeAPIError("timeout")

    assessment = assess_token(client, "addr", "SYM")

    assert assessment.liquidity_usd is None
    assert any("Liquiditäts-" in note for note in assessment.report.unchecked)


@patch("risk_scan.get_mint_authorities")
def test_without_history_connection_no_history_findings_appear(mock_authorities):
    mock_authorities.return_value = MintAuthorities(mint_authority=None, freeze_authority=None)
    assessment = assess_token(_client(), "addr", "SYM", conn=None)
    messages = [f.message for f in assessment.report.findings]
    assert not any("eingebrochen" in m or "Serial-Launcher" in m for m in messages)


@patch("risk_scan.get_mint_authorities")
def test_liquidity_crash_since_previous_scan_is_detected_via_history(mock_authorities):
    mock_authorities.return_value = MintAuthorities(mint_authority=None, freeze_authority=None)
    conn = connect(db_path=":memory:")
    try:
        record_snapshot(conn, Snapshot(
            address="addr", symbol="SYM", scanned_at="2026-09-09T00:00:00+00:00",
            liquidity_usd=100_000, volume_24h_usd=50_000, holder_count=500,
            top10_percent=20.0, score_total=90, risk_level="NIEDRIG",
        ))
        # aktueller Scan: Liquiditaet auf 10% eingebrochen
        client = _client(market_data={"liquidity": 10_000})
        assessment = assess_token(client, "addr", "SYM", conn=conn)
        assert any("eingebrochen" in f.message for f in assessment.report.findings)
    finally:
        conn.close()


@patch("risk_scan.get_mint_authorities")
def test_serial_launcher_pattern_is_detected_via_creator_history(mock_authorities):
    conn = connect(db_path=":memory:")
    try:
        for i in range(5):
            record_snapshot(conn, Snapshot(
                address=f"other-addr-{i}", symbol=f"C{i}", scanned_at="2026-09-09T00:00:00+00:00",
                liquidity_usd=1_000, volume_24h_usd=1_000, holder_count=10,
                top10_percent=90.0, score_total=20, risk_level="HOCH",
                creator_authority="serial-creator",
            ))

        mock_authorities.return_value = MintAuthorities(
            mint_authority=None, freeze_authority=None, update_authority="serial-creator",
        )
        assessment = assess_token(_client(), "new-addr", "NEW", conn=conn)
        assert any("Serial-Launcher" in f.message for f in assessment.report.findings)
    finally:
        conn.close()


@patch("risk_scan.get_mint_authorities")
def test_assess_token_records_a_new_snapshot_when_conn_given(mock_authorities):
    mock_authorities.return_value = MintAuthorities(mint_authority=None, freeze_authority=None)
    conn = connect(db_path=":memory:")
    try:
        assess_token(_client(), "addr", "SYM", conn=conn)
        row = conn.execute("SELECT COUNT(*) FROM snapshots WHERE address = 'addr'").fetchone()
        assert row[0] == 1
    finally:
        conn.close()
