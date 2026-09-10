"""Tests für die Scoring-Logik - laufen offline, ohne API-Zugriff."""
from config import ScoreWeights
from score import compute_score

WEIGHTS = ScoreWeights(
    holder_concentration=25,
    liquidity=20,
    holder_count=15,
    volume_liquidity_ratio=15,
    wash_trading=15,
    transfer_fee=10,
)


def _healthy_score(**overrides):
    params = dict(
        top10_percent=20.0,
        holder_count=1000,
        liquidity_usd=100_000,
        volume_24h_usd=300_000,
        trade_24h=100,
        unique_wallet_24h=80,
        transfer_fee_basis_points=None,
        has_critical_finding=False,
        weights=WEIGHTS,
    )
    params.update(overrides)
    return compute_score(**params)


def test_healthy_coin_scores_at_or_near_100():
    score = _healthy_score()
    assert score.total == 100
    assert not score.capped
    assert score.unscored_factors == []


def test_critical_finding_caps_score_even_with_perfect_metrics():
    score = _healthy_score(has_critical_finding=True)
    assert score.total <= 10
    assert score.capped


def test_high_holder_concentration_reduces_score():
    healthy = _healthy_score().total
    concentrated = _healthy_score(top10_percent=85.0).total
    assert concentrated < healthy


def test_missing_data_is_unscored_not_penalized_to_zero():
    score = _healthy_score(top10_percent=None)
    breakdown = {b.factor: b for b in score.breakdown}
    assert breakdown["Holder-Konzentration"].unscored
    assert breakdown["Holder-Konzentration"].points == 0
    # der Rest bleibt unberuehrt und voll bewertet
    assert breakdown["Liquidität"].points == WEIGHTS.liquidity


def test_missing_liquidity_makes_volume_ratio_unscored_too():
    score = _healthy_score(liquidity_usd=None)
    breakdown = {b.factor: b for b in score.breakdown}
    assert breakdown["Liquidität"].unscored
    assert breakdown["Volumen/Liquidität"].unscored


def test_extreme_transfer_fee_reduces_score_but_does_not_cap():
    score = _healthy_score(transfer_fee_basis_points=2500)
    assert not score.capped
    assert score.total == 90  # volle 100 minus 10 Punkte Transfer-Steuer


def test_score_is_between_0_and_100():
    worst = _healthy_score(
        top10_percent=99.0,
        holder_count=1,
        liquidity_usd=0,
        volume_24h_usd=0,
        trade_24h=1000,
        unique_wallet_24h=1,
        transfer_fee_basis_points=5000,
    )
    assert 0 <= worst.total <= 100
