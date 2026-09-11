"""Tests für die Risiko-Bewertungslogik - laufen offline, ohne API-Zugriff."""
from config import RiskThresholds
from risk import (
    Severity,
    build_report,
    evaluate_authorities,
    evaluate_creator_history,
    evaluate_holder_concentration,
    evaluate_liquidity_and_trading,
    evaluate_liquidity_lock,
    evaluate_liquidity_trend,
    evaluate_token_extensions,
)

THRESHOLDS = RiskThresholds(
    top10_percent_high=70.0,
    top10_percent_medium=50.0,
    min_holder_count=50,
    min_liquidity_usd=2_000,
    max_volume_liquidity_ratio=20.0,
    max_trades_per_wallet=15.0,
)


def test_revoked_authorities_yield_no_findings():
    assert evaluate_authorities(None, None) == []


def test_active_mint_authority_is_critical():
    findings = evaluate_authorities("SomeCreatorAddress", None)
    assert len(findings) == 1
    assert findings[0].severity == Severity.KRITISCH


def test_active_freeze_authority_is_critical():
    findings = evaluate_authorities(None, "SomeCreatorAddress")
    assert len(findings) == 1
    assert findings[0].severity == Severity.KRITISCH


def test_both_authorities_active_yield_two_critical_findings():
    findings = evaluate_authorities("A", "B")
    assert len(findings) == 2
    assert all(f.severity == Severity.KRITISCH for f in findings)


def test_high_holder_concentration_is_hoch():
    findings = evaluate_holder_concentration(85.0, 1000, THRESHOLDS)
    assert any(f.severity == Severity.HOCH for f in findings)


def test_medium_holder_concentration_is_mittel():
    findings = evaluate_holder_concentration(55.0, 1000, THRESHOLDS)
    assert findings == [f for f in findings if f.severity == Severity.MITTEL]
    assert len(findings) == 1


def test_healthy_holder_distribution_yields_no_findings():
    findings = evaluate_holder_concentration(20.0, 1000, THRESHOLDS)
    assert findings == []


def test_too_few_holders_is_flagged_even_with_low_concentration():
    findings = evaluate_holder_concentration(10.0, 5, THRESHOLDS)
    assert len(findings) == 1
    assert "Holder" in findings[0].message


def test_transfer_hook_extension_is_critical():
    findings = evaluate_token_extensions(frozenset({"transferHook"}), None, THRESHOLDS)
    assert len(findings) == 1
    assert findings[0].severity == Severity.KRITISCH


def test_permanent_delegate_extension_is_critical():
    findings = evaluate_token_extensions(frozenset({"permanentDelegate"}), None, THRESHOLDS)
    assert len(findings) == 1
    assert findings[0].severity == Severity.KRITISCH


def test_non_transferable_extension_is_critical():
    findings = evaluate_token_extensions(frozenset({"nonTransferable"}), None, THRESHOLDS)
    assert len(findings) == 1
    assert findings[0].severity == Severity.KRITISCH


def test_harmless_extensions_yield_no_findings():
    findings = evaluate_token_extensions(frozenset({"metadataPointer", "tokenMetadata"}), None, THRESHOLDS)
    assert findings == []


def test_mutable_token_metadata_is_mittel():
    findings = evaluate_token_extensions(
        frozenset({"metadataPointer", "tokenMetadata"}), None, THRESHOLDS, update_authority="CreatorWallet"
    )
    assert len(findings) == 1
    assert findings[0].severity == Severity.MITTEL
    assert "Rebrand" in findings[0].message


def test_immutable_token_metadata_is_not_flagged():
    findings = evaluate_token_extensions(
        frozenset({"metadataPointer", "tokenMetadata"}), None, THRESHOLDS, update_authority=None
    )
    assert findings == []


def test_update_authority_without_token_metadata_extension_is_not_flagged():
    # update_authority stammt aus derselben Extension - ohne "tokenMetadata" in
    # den Extensions kann das praktisch nicht vorkommen, aber defensiv geprüft
    findings = evaluate_token_extensions(frozenset(), None, THRESHOLDS, update_authority="CreatorWallet")
    assert findings == []


def test_normal_transfer_fee_is_not_flagged():
    findings = evaluate_token_extensions(frozenset({"transferFeeConfig"}), 100, THRESHOLDS)
    assert findings == []


def test_elevated_transfer_fee_is_mittel():
    findings = evaluate_token_extensions(frozenset({"transferFeeConfig"}), 500, THRESHOLDS)
    assert len(findings) == 1
    assert findings[0].severity == Severity.MITTEL


def test_extreme_transfer_fee_is_hoch():
    findings = evaluate_token_extensions(frozenset({"transferFeeConfig"}), 2500, THRESHOLDS)
    assert len(findings) == 1
    assert findings[0].severity == Severity.HOCH


def test_unknown_liquidity_lock_status_yields_no_finding():
    # None = kein klassischer LP-Token (z.B. noch auf Bonding Curve) - kein Risiko-Signal
    assert evaluate_liquidity_lock(None, THRESHOLDS) == []


def test_well_locked_liquidity_yields_no_finding():
    assert evaluate_liquidity_lock(95.0, THRESHOLDS) == []


def test_partially_locked_liquidity_is_mittel():
    findings = evaluate_liquidity_lock(65.0, THRESHOLDS)
    assert len(findings) == 1
    assert findings[0].severity == Severity.MITTEL


def test_mostly_unlocked_liquidity_is_hoch():
    findings = evaluate_liquidity_lock(20.0, THRESHOLDS)
    assert len(findings) == 1
    assert findings[0].severity == Severity.HOCH
    assert "Rug-Pull" in findings[0].message


def test_low_liquidity_is_hoch():
    findings = evaluate_liquidity_and_trading(
        liquidity_usd=100, volume_24h_usd=500, trade_24h=10, unique_wallet_24h=8, thresholds=THRESHOLDS
    )
    assert any(f.severity == Severity.HOCH for f in findings)


def test_extreme_volume_to_liquidity_ratio_is_flagged():
    findings = evaluate_liquidity_and_trading(
        liquidity_usd=5_000, volume_24h_usd=500_000, trade_24h=10, unique_wallet_24h=8, thresholds=THRESHOLDS
    )
    assert any("Verhältnis" in f.message for f in findings)


def test_wash_trade_pattern_is_flagged():
    findings = evaluate_liquidity_and_trading(
        liquidity_usd=10_000, volume_24h_usd=10_000, trade_24h=500, unique_wallet_24h=5, thresholds=THRESHOLDS
    )
    assert any("Wash-Trade" in f.message for f in findings)


def test_healthy_liquidity_and_trading_yields_no_findings():
    findings = evaluate_liquidity_and_trading(
        liquidity_usd=50_000, volume_24h_usd=100_000, trade_24h=200, unique_wallet_24h=150, thresholds=THRESHOLDS
    )
    assert findings == []


def test_no_previous_coins_from_creator_yields_no_findings():
    assert evaluate_creator_history(0, THRESHOLDS) == []
    assert evaluate_creator_history(1, THRESHOLDS) == []


def test_a_few_previous_coins_from_creator_is_mittel():
    findings = evaluate_creator_history(2, THRESHOLDS)
    assert len(findings) == 1
    assert findings[0].severity == Severity.MITTEL


def test_many_previous_coins_from_creator_is_hoch():
    findings = evaluate_creator_history(5, THRESHOLDS)
    assert len(findings) == 1
    assert findings[0].severity == Severity.HOCH


def test_creator_with_a_rugged_previous_coin_is_kritisch():
    findings = evaluate_creator_history(1, THRESHOLDS, rugged_coin_count=1)
    assert len(findings) == 1
    assert findings[0].severity == Severity.KRITISCH


def test_rugged_previous_coin_outranks_the_serial_launcher_count():
    # selbst bei nur einem einzigen bisherigen Coin ist ein Rug-Nachweis
    # schwerwiegender als die reine "viele Coins gelistet"-Zaehlung
    findings = evaluate_creator_history(1, THRESHOLDS, rugged_coin_count=1)
    assert findings[0].severity == Severity.KRITISCH


def test_no_rugged_previous_coins_falls_back_to_count_based_findings():
    findings = evaluate_creator_history(5, THRESHOLDS, rugged_coin_count=0)
    assert len(findings) == 1
    assert findings[0].severity == Severity.HOCH


def test_liquidity_trend_without_previous_snapshot_yields_no_findings():
    assert evaluate_liquidity_trend(10_000, None, None, THRESHOLDS) == []


def test_liquidity_trend_with_zero_previous_liquidity_yields_no_findings():
    assert evaluate_liquidity_trend(10_000, 0, 5, THRESHOLDS) == []


def test_liquidity_crash_is_hoch():
    findings = evaluate_liquidity_trend(4_000, 10_000, 5, THRESHOLDS)  # -60%
    assert len(findings) == 1
    assert findings[0].severity == Severity.HOCH
    assert "eingebrochen" in findings[0].message


def test_moderate_liquidity_drop_is_mittel():
    findings = evaluate_liquidity_trend(7_500, 10_000, 5, THRESHOLDS)  # -25%
    assert len(findings) == 1
    assert findings[0].severity == Severity.MITTEL


def test_liquidity_growth_yields_no_findings():
    assert evaluate_liquidity_trend(15_000, 10_000, 5, THRESHOLDS) == []


def test_build_report_overall_is_highest_finding_severity():
    from risk import RiskFinding

    findings = [RiskFinding(Severity.MITTEL, "a"), RiskFinding(Severity.KRITISCH, "b")]
    report = build_report("addr", "SYM", findings, [])
    assert report.overall == Severity.KRITISCH


def test_build_report_with_no_findings_is_niedrig():
    report = build_report("addr", "SYM", [], [])
    assert report.overall == Severity.NIEDRIG
