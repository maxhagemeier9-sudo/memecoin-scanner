"""Integrationstests für assess_listing/scan_new_coins/recheck_watchlist:
prüft die Verdrahtung zwischen GeckoTerminal, Helius-RPC, Historie und den
reinen Bewertungsfunktionen - alles gemockt, keine Live-Calls.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from geckoterminal_client import GeckoTerminalAPIError, PoolListing
from history import Snapshot, connect, record_snapshot
from risk import Severity, build_report
from risk_scan import (
    TokenAssessment,
    assess_listing,
    is_rising,
    recheck_watchlist,
    scan_new_coins,
    scan_trending_coins,
)
from score import Score
from solana_rpc import MintAuthorities, SolanaRPCError


def _listing(
    token_address="addr", symbol="SYM", pool_address="pool1",
    price_usd=1.0, liquidity_usd=100_000.0, volume_24h_usd=200_000.0,
    trade_24h=100, unique_wallet_24h=80, created_at="2026-09-10T00:00:00Z",
    locked_liquidity_percentage=None,
) -> PoolListing:
    return PoolListing(
        pool_address=pool_address, token_address=token_address, symbol=symbol,
        created_at=created_at, price_usd=price_usd, liquidity_usd=liquidity_usd,
        volume_24h_usd=volume_24h_usd, trade_24h=trade_24h, unique_wallet_24h=unique_wallet_24h,
        locked_liquidity_percentage=locked_liquidity_percentage,
    )


def _pool_dict(token_address="addr", pool_address="pool1", symbol="SYM", pool_created_at="2026-09-10T00:00:00Z"):
    return {
        "attributes": {
            "address": pool_address,
            "name": f"{symbol} / SOL",
            "pool_created_at": pool_created_at,
            "base_token_price_usd": "1.0",
            "reserve_in_usd": "100000.0",
            "volume_usd": {"h24": "200000.0"},
            "transactions": {"h24": {"buys": 60, "sells": 40, "buyers": 50, "sellers": 30}},
        },
        "relationships": {
            "base_token": {"data": {"id": f"solana_{token_address}"}},
            "dex": {"data": {"id": "pump-fun"}},
        },
    }


@pytest.fixture(autouse=True)
def _no_real_sleep():
    with patch("risk_scan.time.sleep", return_value=None):
        yield


@patch("risk_scan.get_top10_concentration")
@patch("risk_scan.get_mint_authorities")
def test_healthy_token_produces_high_score_and_no_findings(mock_authorities, mock_top10):
    mock_authorities.return_value = MintAuthorities(mint_authority=None, freeze_authority=None, total_supply=1_000_000.0)
    mock_top10.return_value = 10.0
    assessment = assess_listing(_listing())
    assert assessment.report.overall == Severity.NIEDRIG
    assert assessment.score.total >= 80
    assert not assessment.score.capped


@patch("risk_scan.get_top10_concentration")
@patch("risk_scan.get_mint_authorities")
def test_mostly_unlocked_liquidity_is_flagged(mock_authorities, mock_top10):
    mock_authorities.return_value = MintAuthorities(mint_authority=None, freeze_authority=None, total_supply=1_000_000.0)
    mock_top10.return_value = 10.0
    assessment = assess_listing(_listing(locked_liquidity_percentage=10.0))
    assert any("gesperrt" in f.message for f in assessment.report.findings)
    assert assessment.report.overall == Severity.HOCH


@patch("risk_scan.get_top10_concentration")
@patch("risk_scan.get_mint_authorities")
def test_active_mint_authority_caps_score(mock_authorities, mock_top10):
    mock_authorities.return_value = MintAuthorities(mint_authority="creator", freeze_authority=None, total_supply=1_000_000.0)
    mock_top10.return_value = 10.0
    assessment = assess_listing(_listing())
    assert assessment.report.overall == Severity.KRITISCH
    assert assessment.score.capped
    assert assessment.score.total <= 10


@patch("risk_scan.get_mint_authorities")
def test_rpc_failure_is_recorded_as_unchecked_without_crashing(mock_authorities):
    mock_authorities.side_effect = SolanaRPCError("timeout")
    assessment = assess_listing(_listing())
    assert any("Mint-" in note for note in assessment.report.unchecked)
    # Liquiditaets-/Handelsdaten kommen aus dem Listing, nicht aus RPC -> Score trotzdem > 0
    assert assessment.score.total > 0


@patch("risk_scan.get_top10_concentration")
@patch("risk_scan.get_mint_authorities")
def test_top10_failure_leaves_that_factor_unscored(mock_authorities, mock_top10):
    mock_authorities.return_value = MintAuthorities(mint_authority=None, freeze_authority=None, total_supply=1_000_000.0)
    mock_top10.side_effect = SolanaRPCError("rate limited")

    assessment = assess_listing(_listing())

    assert any("Holder-Konzentration" in note for note in assessment.report.unchecked)
    assert "Holder-Konzentration" in assessment.score.unscored_factors


@patch("risk_scan.get_top10_concentration")
@patch("risk_scan.get_mint_authorities")
def test_null_liquidity_is_treated_as_zero(mock_authorities, mock_top10):
    mock_authorities.return_value = MintAuthorities(mint_authority=None, freeze_authority=None, total_supply=1_000_000.0)
    mock_top10.return_value = 10.0

    listing = _listing(liquidity_usd=None)
    assessment = assess_listing(listing)

    assert assessment.liquidity_usd == 0


@patch("risk_scan.get_top10_concentration")
@patch("risk_scan.get_mint_authorities")
def test_without_history_connection_no_history_findings_appear(mock_authorities, mock_top10):
    mock_authorities.return_value = MintAuthorities(mint_authority=None, freeze_authority=None, total_supply=1_000_000.0)
    mock_top10.return_value = 10.0
    assessment = assess_listing(_listing(), conn=None)
    messages = [f.message for f in assessment.report.findings]
    assert not any("eingebrochen" in m or "Serial-Launcher" in m for m in messages)


@patch("risk_scan.get_top10_concentration")
@patch("risk_scan.get_mint_authorities")
def test_liquidity_crash_since_previous_scan_is_detected_via_history(mock_authorities, mock_top10):
    mock_authorities.return_value = MintAuthorities(mint_authority=None, freeze_authority=None, total_supply=1_000_000.0)
    mock_top10.return_value = 10.0
    conn = connect(db_path=":memory:")
    try:
        record_snapshot(conn, Snapshot(
            address="addr", symbol="SYM", scanned_at="2026-09-09T00:00:00+00:00",
            liquidity_usd=100_000, volume_24h_usd=50_000, holder_count=None,
            top10_percent=20.0, score_total=90, risk_level="NIEDRIG",
        ))
        listing = _listing(liquidity_usd=10_000.0)  # eingebrochen von 100k auf 10k
        assessment = assess_listing(listing, conn=conn)
        assert any("eingebrochen" in f.message for f in assessment.report.findings)
    finally:
        conn.close()


@patch("risk_scan.get_top10_concentration")
@patch("risk_scan.get_mint_authorities")
def test_serial_launcher_pattern_is_detected_via_creator_history(mock_authorities, mock_top10):
    mock_top10.return_value = 10.0
    conn = connect(db_path=":memory:")
    try:
        for i in range(5):
            record_snapshot(conn, Snapshot(
                address=f"other-addr-{i}", symbol=f"C{i}", scanned_at="2026-09-09T00:00:00+00:00",
                liquidity_usd=1_000, volume_24h_usd=1_000, holder_count=None,
                top10_percent=90.0, score_total=20, risk_level="HOCH",
                creator_authority="serial-creator",
            ))

        mock_authorities.return_value = MintAuthorities(
            mint_authority=None, freeze_authority=None, update_authority="serial-creator", total_supply=1_000_000.0,
        )
        assessment = assess_listing(_listing(token_address="new-addr"), conn=conn)
        assert any("Serial-Launcher" in f.message for f in assessment.report.findings)
    finally:
        conn.close()


@patch("risk_scan.get_top10_concentration")
@patch("risk_scan.get_mint_authorities")
def test_creator_with_a_previously_rugged_coin_is_kritisch(mock_authorities, mock_top10):
    mock_top10.return_value = 10.0
    conn = connect(db_path=":memory:")
    try:
        record_snapshot(conn, Snapshot(
            address="rugged-addr", symbol="RUGGED", scanned_at="2026-09-09T00:00:00+00:00",
            liquidity_usd=50_000, volume_24h_usd=1_000, holder_count=None,
            top10_percent=50.0, score_total=50, risk_level="MITTEL",
            creator_authority="known-rugger",
        ))
        record_snapshot(conn, Snapshot(
            address="rugged-addr", symbol="RUGGED", scanned_at="2026-09-09T01:00:00+00:00",
            liquidity_usd=500, volume_24h_usd=100, holder_count=None,  # -99%
            top10_percent=90.0, score_total=10, risk_level="KRITISCH",
            creator_authority="known-rugger",
        ))

        mock_authorities.return_value = MintAuthorities(
            mint_authority=None, freeze_authority=None, update_authority="known-rugger", total_supply=1_000_000.0,
        )
        assessment = assess_listing(_listing(token_address="new-addr"), conn=conn)
        assert any("Rug-Pull-Muster" in f.message for f in assessment.report.findings)
        assert assessment.report.overall == Severity.KRITISCH
    finally:
        conn.close()


@patch("risk_scan.get_top10_concentration")
@patch("risk_scan.get_mint_authorities")
def test_assess_listing_records_a_new_snapshot_with_pool_address(mock_authorities, mock_top10):
    mock_authorities.return_value = MintAuthorities(mint_authority=None, freeze_authority=None, total_supply=1_000_000.0)
    mock_top10.return_value = 10.0
    conn = connect(db_path=":memory:")
    try:
        assess_listing(_listing(token_address="addr", pool_address="pool-xyz"), conn=conn)
        row = conn.execute("SELECT COUNT(*), pool_address FROM snapshots WHERE address = 'addr'").fetchone()
        assert row[0] == 1
        assert row[1] == "pool-xyz"
    finally:
        conn.close()


def test_is_rising_true_when_liquidity_grew_enough_and_risk_acceptable():
    first = Snapshot(
        address="addr", symbol="SYM", scanned_at="t", liquidity_usd=1_000.0, volume_24h_usd=1_000.0,
        holder_count=None, top10_percent=20.0, score_total=50, risk_level="MITTEL",
    )
    report = build_report("addr", "SYM", [], [])
    score = Score(total=60, capped=False, breakdown=[])
    assessment = TokenAssessment(report=report, score=score, liquidity_usd=2_000.0)  # +100%
    assert is_rising(first, assessment) is True


@patch("risk_scan.get_top10_concentration")
@patch("risk_scan.get_mint_authorities")
def test_scan_new_coins_paginates_and_dedupes_by_token_address(mock_authorities, mock_top10):
    mock_authorities.return_value = MintAuthorities(mint_authority=None, freeze_authority=None, total_supply=1_000_000.0)
    mock_top10.return_value = 10.0

    client = MagicMock()
    page_1 = [_pool_dict(token_address=f"addr-{i}", pool_address=f"pool-{i}") for i in range(20)]
    page_2 = [_pool_dict(token_address=f"addr-{i}", pool_address=f"pool-{i}") for i in range(10, 30)]  # 10-19 doppelt
    client.get_new_pools.side_effect = [page_1, page_2]

    results = scan_new_coins(client, pages=2)

    assert len(results) == 30  # 0-29, dedupliziert trotz Overlap 10-19
    addresses = [listing["address"] for listing, _ in results]
    assert len(addresses) == len(set(addresses))


@patch("risk_scan.get_top10_concentration")
@patch("risk_scan.get_mint_authorities")
def test_scan_trending_coins_assesses_relatively_new_pools(mock_authorities, mock_top10):
    mock_authorities.return_value = MintAuthorities(mint_authority=None, freeze_authority=None, total_supply=1_000_000.0)
    mock_top10.return_value = 10.0

    fresh = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    client = MagicMock()
    client.get_trending_pools.return_value = [_pool_dict(token_address="addr", pool_created_at=fresh)]

    results = scan_trending_coins(client, pages=1)

    assert len(results) == 1
    assert results[0][0]["address"] == "addr"
    client.get_trending_pools.assert_called_once_with(duration="1h", page=1)


@patch("risk_scan.get_top10_concentration")
@patch("risk_scan.get_mint_authorities")
def test_scan_trending_coins_skips_pools_older_than_max_age(mock_authorities, mock_top10):
    mock_authorities.return_value = MintAuthorities(mint_authority=None, freeze_authority=None, total_supply=1_000_000.0)
    mock_top10.return_value = 10.0

    old = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    client = MagicMock()
    client.get_trending_pools.return_value = [_pool_dict(token_address="addr", pool_created_at=old)]

    results = scan_trending_coins(client, pages=1, max_age_minutes=24 * 60)

    assert results == []


@patch("risk_scan.get_top10_concentration")
@patch("risk_scan.get_mint_authorities")
def test_scan_trending_coins_skips_excluded_addresses(mock_authorities, mock_top10):
    mock_authorities.return_value = MintAuthorities(mint_authority=None, freeze_authority=None, total_supply=1_000_000.0)
    mock_top10.return_value = 10.0

    fresh = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    client = MagicMock()
    client.get_trending_pools.return_value = [_pool_dict(token_address="already-seen", pool_created_at=fresh)]

    results = scan_trending_coins(client, pages=1, exclude_addresses={"already-seen"})

    assert results == []
    mock_authorities.assert_not_called()  # gar nicht erst bewertet, spart RPC-Calls


@patch("risk_scan.get_top10_concentration")
@patch("risk_scan.get_mint_authorities")
def test_scan_trending_coins_invokes_on_assessed_immediately(mock_authorities, mock_top10):
    mock_authorities.return_value = MintAuthorities(mint_authority=None, freeze_authority=None, total_supply=1_000_000.0)
    mock_top10.return_value = 10.0

    fresh = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    client = MagicMock()
    client.get_trending_pools.return_value = [_pool_dict(token_address="addr", pool_created_at=fresh)]

    seen = []
    scan_trending_coins(client, pages=1, on_assessed=lambda listing, a: seen.append(listing["address"]))

    assert seen == ["addr"]


@patch("risk_scan.get_top10_concentration")
@patch("risk_scan.get_mint_authorities")
def test_scan_new_coins_invokes_on_assessed_immediately_per_coin(mock_authorities, mock_top10):
    """on_assessed feuert PRO Coin sofort nach dessen Bewertung, nicht erst
    nach dem kompletten Batch - Grundlage dafür, dass monitor.scan_once()
    Alerts ohne Wartezeit auf die restlichen Coins verschicken kann."""
    mock_authorities.return_value = MintAuthorities(mint_authority=None, freeze_authority=None, total_supply=1_000_000.0)
    mock_top10.return_value = 10.0

    client = MagicMock()
    client.get_new_pools.return_value = [_pool_dict(token_address=f"addr-{i}", pool_address=f"pool-{i}") for i in range(5)]

    seen_during_call: list[str] = []

    def _on_assessed(listing, assessment):
        seen_during_call.append(listing["address"])
        # Zum Zeitpunkt des Callbacks fuer den ERSTEN Coin duerfen die
        # spaeteren Coins noch NICHT bewertet worden sein.
        if listing["address"] == "addr-0":
            assert seen_during_call == ["addr-0"]

    results = scan_new_coins(client, pages=1, on_assessed=_on_assessed)

    assert seen_during_call == [f"addr-{i}" for i in range(5)]
    assert len(results) == 5


@patch("risk_scan.get_top10_concentration")
@patch("risk_scan.get_mint_authorities")
def test_recheck_watchlist_invokes_on_assessed_immediately_per_coin(mock_authorities, mock_top10):
    mock_authorities.return_value = MintAuthorities(mint_authority=None, freeze_authority=None, total_supply=1_000_000.0)
    mock_top10.return_value = 10.0

    conn = connect(db_path=":memory:")
    try:
        record_snapshot(conn, Snapshot(
            address="addr", symbol="SYM", scanned_at="2026-09-09T00:00:00+00:00",
            liquidity_usd=1_000.0, volume_24h_usd=1_000.0, holder_count=None,
            top10_percent=20.0, score_total=50, risk_level="MITTEL", pool_address="pool-xyz",
        ))
        record_snapshot(conn, Snapshot(
            address="addr", symbol="SYM", scanned_at="2026-09-09T00:10:00+00:00",
            liquidity_usd=1_500.0, volume_24h_usd=1_000.0, holder_count=None,
            top10_percent=20.0, score_total=55, risk_level="MITTEL", pool_address="pool-xyz",
        ))

        client = MagicMock()
        client.get_pool.return_value = _pool_dict(token_address="addr", pool_address="pool-xyz")

        called = []
        recheck_watchlist(client, conn, limit=10, on_assessed=lambda first, a: called.append(a.report.address))

        assert called == ["addr"]
    finally:
        conn.close()


@patch("risk_scan.get_top10_concentration")
@patch("risk_scan.get_mint_authorities")
def test_recheck_watchlist_fetches_pool_and_reassesses(mock_authorities, mock_top10):
    mock_authorities.return_value = MintAuthorities(mint_authority=None, freeze_authority=None, total_supply=1_000_000.0)
    mock_top10.return_value = 10.0

    conn = connect(db_path=":memory:")
    try:
        record_snapshot(conn, Snapshot(
            address="addr", symbol="SYM", scanned_at="2026-09-09T00:00:00+00:00",
            liquidity_usd=1_000.0, volume_24h_usd=1_000.0, holder_count=None,
            top10_percent=20.0, score_total=50, risk_level="MITTEL", pool_address="pool-xyz",
        ))
        record_snapshot(conn, Snapshot(
            address="addr", symbol="SYM", scanned_at="2026-09-09T00:10:00+00:00",
            liquidity_usd=1_500.0, volume_24h_usd=1_000.0, holder_count=None,
            top10_percent=20.0, score_total=55, risk_level="MITTEL", pool_address="pool-xyz",
        ))

        client = MagicMock()
        client.get_pool.return_value = _pool_dict(token_address="addr", pool_address="pool-xyz")

        results = recheck_watchlist(client, conn, limit=10)

        assert len(results) == 1
        first, assessment = results[0]
        assert first.liquidity_usd == 1_000.0  # allererster Snapshot
        client.get_pool.assert_called_once_with("pool-xyz")
    finally:
        conn.close()


def test_recheck_watchlist_skips_candidates_without_pool_address():
    conn = connect(db_path=":memory:")
    try:
        record_snapshot(conn, Snapshot(
            address="addr", symbol="SYM", scanned_at="2026-09-09T00:00:00+00:00",
            liquidity_usd=1_000.0, volume_24h_usd=1_000.0, holder_count=None,
            top10_percent=20.0, score_total=50, risk_level="MITTEL", pool_address=None,
        ))
        record_snapshot(conn, Snapshot(
            address="addr", symbol="SYM", scanned_at="2026-09-09T00:10:00+00:00",
            liquidity_usd=1_500.0, volume_24h_usd=1_000.0, holder_count=None,
            top10_percent=20.0, score_total=55, risk_level="MITTEL", pool_address=None,
        ))

        client = MagicMock()
        results = recheck_watchlist(client, conn, limit=10)

        assert results == []
        client.get_pool.assert_not_called()
    finally:
        conn.close()


def test_recheck_watchlist_survives_pool_lookup_failure():
    conn = connect(db_path=":memory:")
    try:
        record_snapshot(conn, Snapshot(
            address="addr", symbol="SYM", scanned_at="2026-09-09T00:00:00+00:00",
            liquidity_usd=1_000.0, volume_24h_usd=1_000.0, holder_count=None,
            top10_percent=20.0, score_total=50, risk_level="MITTEL", pool_address="pool-xyz",
        ))
        record_snapshot(conn, Snapshot(
            address="addr", symbol="SYM", scanned_at="2026-09-09T00:10:00+00:00",
            liquidity_usd=1_500.0, volume_24h_usd=1_000.0, holder_count=None,
            top10_percent=20.0, score_total=55, risk_level="MITTEL", pool_address="pool-xyz",
        ))

        client = MagicMock()
        client.get_pool.side_effect = GeckoTerminalAPIError("rate limited")

        results = recheck_watchlist(client, conn, limit=10)  # darf nicht crashen
        assert results == []
    finally:
        conn.close()
