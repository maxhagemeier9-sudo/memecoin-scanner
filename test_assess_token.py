"""Integrationstests für assess_token: prüft die Verdrahtung zwischen
Birdeye-Client, Solana-RPC, Historie und den reinen Bewertungsfunktionen -
alles gemockt, keine Live-Calls.
"""
from unittest.mock import MagicMock, call, patch

import pytest

from birdeye_client import BirdeyeAPIError
from history import Snapshot, connect, record_snapshot
from risk import Severity
from risk_scan import TokenAssessment, _listing_label, assess_token, is_rising, recheck_watchlist, scan_new_coins
from score import Score
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


def test_listing_label_prefers_symbol_over_name():
    assert _listing_label({"symbol": "ABC", "name": "Abc Coin"}) == "ABC"


def test_listing_label_falls_back_to_name_when_symbol_missing():
    assert _listing_label({"symbol": None, "name": "Abc Coin"}) == "Abc Coin"


def test_listing_label_falls_back_to_placeholder_when_both_missing():
    assert _listing_label({"symbol": None, "name": None}) == "???"


def _snapshot(address="addr", liquidity_usd=1_000.0, risk_level="MITTEL", scanned_at="2026-09-09T00:00:00+00:00"):
    return Snapshot(
        address=address, symbol="SYM", scanned_at=scanned_at, liquidity_usd=liquidity_usd,
        volume_24h_usd=1_000.0, holder_count=50, top10_percent=40.0, score_total=50,
        risk_level=risk_level,
    )


def _assessment(overall=Severity.MITTEL, liquidity_usd=1_500.0, address="addr"):
    from risk import build_report

    report = build_report(address, "SYM", [], [])
    object.__setattr__(report, "overall", overall)  # RiskReport ist frozen
    score = Score(total=60, capped=False, breakdown=[])
    return TokenAssessment(report=report, score=score, liquidity_usd=liquidity_usd)


def test_is_rising_true_when_liquidity_grew_enough_and_risk_acceptable():
    first = _snapshot(liquidity_usd=1_000.0)
    assessment = _assessment(overall=Severity.MITTEL, liquidity_usd=2_000.0)  # +100%
    assert is_rising(first, assessment) is True


def test_is_rising_false_when_growth_too_small():
    first = _snapshot(liquidity_usd=1_000.0)
    assessment = _assessment(overall=Severity.MITTEL, liquidity_usd=1_100.0)  # +10%
    assert is_rising(first, assessment) is False


def test_is_rising_false_when_risk_is_hoch():
    first = _snapshot(liquidity_usd=1_000.0)
    assessment = _assessment(overall=Severity.HOCH, liquidity_usd=5_000.0)  # starkes Wachstum, aber riskant
    assert is_rising(first, assessment) is False


def test_is_rising_false_when_first_liquidity_missing():
    first = _snapshot(liquidity_usd=None)
    assessment = _assessment(liquidity_usd=5_000.0)
    assert is_rising(first, assessment) is False


@patch("risk_scan.get_mint_authorities")
def test_recheck_watchlist_rechecks_known_candidates_and_returns_first_and_current(mock_authorities):
    mock_authorities.return_value = MintAuthorities(mint_authority=None, freeze_authority=None)
    conn = connect(db_path=":memory:")
    try:
        record_snapshot(conn, _snapshot(address="addr1", liquidity_usd=1_000.0, scanned_at="2026-09-09T00:00:00+00:00"))
        record_snapshot(conn, _snapshot(address="addr1", liquidity_usd=1_500.0, scanned_at="2026-09-09T00:10:00+00:00"))

        client = _client(market_data={"liquidity": 3_000.0})
        results = recheck_watchlist(client, conn, limit=10)

        assert len(results) == 1
        first, assessment = results[0]
        assert first.liquidity_usd == 1_000.0  # allererster Snapshot, nicht der letzte
        assert assessment.liquidity_usd == 3_000.0
    finally:
        conn.close()


@patch("risk_scan.get_mint_authorities")
def test_recheck_watchlist_ignores_addresses_seen_only_once(mock_authorities):
    mock_authorities.return_value = MintAuthorities(mint_authority=None, freeze_authority=None)
    conn = connect(db_path=":memory:")
    try:
        record_snapshot(conn, _snapshot(address="addr1"))  # nur 1x
        results = recheck_watchlist(_client(), conn, limit=10)
        assert results == []
    finally:
        conn.close()


@patch("risk_scan.get_mint_authorities")
def test_scan_new_coins_paginates_across_multiple_pages(mock_authorities):
    mock_authorities.return_value = MintAuthorities(mint_authority=None, freeze_authority=None)
    client = _client()

    page_1 = [{"address": f"addr-{i}", "symbol": f"C{i}", "liquidityAddedAt": "2026-09-09T00:00:00"} for i in range(20)]
    page_2 = [{"address": f"addr-{i}", "symbol": f"C{i}", "liquidityAddedAt": "2026-09-09T00:00:00"} for i in range(20, 40)]
    client.get_new_listings.side_effect = [page_1, page_2]

    results = scan_new_coins(client, limit=20, pages=2)

    assert len(results) == 40
    assert client.get_new_listings.call_args_list == [
        call(limit=20, offset=0),
        call(limit=20, offset=20),
    ]
