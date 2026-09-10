"""Reine Scoring-Logik: kombiniert Scanner- und Risk-Scanner-Daten zu einem
0-100-Score pro Coin. Nimmt bereits abgerufene Werte entgegen, macht selbst
keine API-Calls - offline testbar, analog zu filters.py/risk.py.

Aufbau (siehe Absprache mit dem Nutzer):
1. Sicherheits-Cap: liegt mindestens ein KRITISCH-Risk-Finding vor (Mint-/
   Freeze-Authority aktiv, TransferHook, PermanentDelegate, NonTransferable),
   wird der Score unabhängig von allen anderen Faktoren auf
   `CRITICAL_SCORE_CAP` gedeckelt. Ein Rug-Vektor darf nie von guten
   Marktzahlen überstrahlt werden.
2. Ohne Cap-Auslösung: 6 gewichtete Faktoren (Gewichte in config.py,
   Summe 100), jeweils in Stufen bewertet statt mit einer stetigen Formel -
   einfacher nachvollziehbar.

Fehlende Einzelwerte (z.B. weil ein API-Call fehlgeschlagen ist) werden
weder positiv noch negativ gewertet ("unscored"), fließen aber sichtbar in
den Report ein, damit ein Score auf dünner Datenbasis nicht überzeugender
wirkt, als er ist.
"""
from __future__ import annotations

from dataclasses import dataclass

from config import CRITICAL_SCORE_CAP, DEFAULT_SCORE_WEIGHTS, ScoreWeights


@dataclass(frozen=True)
class ScoreBreakdown:
    factor: str
    points: float
    max_points: int
    unscored: bool = False


@dataclass(frozen=True)
class Score:
    total: int
    capped: bool
    breakdown: list[ScoreBreakdown]

    @property
    def unscored_factors(self) -> list[str]:
        return [b.factor for b in self.breakdown if b.unscored]


def score_holder_concentration(top10_percent: float | None, weight: int) -> ScoreBreakdown:
    if top10_percent is None:
        return ScoreBreakdown("Holder-Konzentration", 0, weight, unscored=True)

    if top10_percent < 30:
        points = weight
    elif top10_percent < 50:
        points = weight * 0.6
    elif top10_percent < 70:
        points = weight * 0.2
    else:
        points = 0

    return ScoreBreakdown("Holder-Konzentration", points, weight)


def score_liquidity(liquidity_usd: float | None, weight: int) -> ScoreBreakdown:
    if liquidity_usd is None:
        return ScoreBreakdown("Liquidität", 0, weight, unscored=True)

    if liquidity_usd >= 50_000:
        points = weight
    elif liquidity_usd >= 10_000:
        points = weight * 0.7
    elif liquidity_usd >= 2_000:
        points = weight * 0.3
    else:
        points = 0

    return ScoreBreakdown("Liquidität", points, weight)


def score_holder_count(holder_count: int | None, weight: int) -> ScoreBreakdown:
    if holder_count is None:
        return ScoreBreakdown("Holder-Anzahl", 0, weight, unscored=True)

    if holder_count >= 500:
        points = weight
    elif holder_count >= 100:
        points = weight * 0.6
    elif holder_count >= 50:
        points = weight * 0.3
    else:
        points = 0

    return ScoreBreakdown("Holder-Anzahl", points, weight)


def score_volume_liquidity_ratio(
    liquidity_usd: float | None,
    volume_24h_usd: float | None,
    weight: int,
) -> ScoreBreakdown:
    if liquidity_usd is None or volume_24h_usd is None or liquidity_usd <= 0:
        return ScoreBreakdown("Volumen/Liquidität", 0, weight, unscored=True)

    ratio = volume_24h_usd / liquidity_usd

    if 0.5 <= ratio <= 10:
        points = weight  # gesunder Bereich: aktiv gehandelt, aber nicht leicht drehbar
    elif ratio > 20:
        points = 0  # deckt sich mit dem HOCH/MITTEL-Schwellwert in risk.py
    else:
        points = weight * 0.4  # entweder recht tot (< 0.5) oder erhöht (10-20)

    return ScoreBreakdown("Volumen/Liquidität", points, weight)


def score_wash_trading(
    trade_24h: int | None,
    unique_wallet_24h: int | None,
    weight: int,
) -> ScoreBreakdown:
    if trade_24h is None or unique_wallet_24h is None or unique_wallet_24h == 0:
        return ScoreBreakdown("Trades/Wallet", 0, weight, unscored=True)

    ratio = trade_24h / unique_wallet_24h

    if ratio <= 3:
        points = weight
    elif ratio <= 8:
        points = weight * 0.6
    elif ratio <= 15:
        points = weight * 0.3
    else:
        points = 0

    return ScoreBreakdown("Trades/Wallet", points, weight)


def score_transfer_fee(transfer_fee_basis_points: int | None, weight: int) -> ScoreBreakdown:
    if transfer_fee_basis_points is None:
        # Keine transferFeeConfig-Extension aktiv - bestmöglicher Fall, nicht "unscored"
        return ScoreBreakdown("Transfer-Steuer", weight, weight)

    if transfer_fee_basis_points < 100:
        points = weight
    elif transfer_fee_basis_points < 300:
        points = weight * 0.7
    elif transfer_fee_basis_points < 1000:
        points = weight * 0.3
    else:
        points = 0

    return ScoreBreakdown("Transfer-Steuer", points, weight)


def compute_score(
    *,
    top10_percent: float | None,
    holder_count: int | None,
    liquidity_usd: float | None,
    volume_24h_usd: float | None,
    trade_24h: int | None,
    unique_wallet_24h: int | None,
    transfer_fee_basis_points: int | None,
    has_critical_finding: bool,
    weights: ScoreWeights = DEFAULT_SCORE_WEIGHTS,
    cap: int = CRITICAL_SCORE_CAP,
) -> Score:
    breakdown = [
        score_holder_concentration(top10_percent, weights.holder_concentration),
        score_liquidity(liquidity_usd, weights.liquidity),
        score_holder_count(holder_count, weights.holder_count),
        score_volume_liquidity_ratio(liquidity_usd, volume_24h_usd, weights.volume_liquidity_ratio),
        score_wash_trading(trade_24h, unique_wallet_24h, weights.wash_trading),
        score_transfer_fee(transfer_fee_basis_points, weights.transfer_fee),
    ]

    total = round(sum(b.points for b in breakdown))
    if has_critical_finding:
        total = min(total, cap)

    return Score(total=total, capped=has_critical_finding, breakdown=breakdown)
