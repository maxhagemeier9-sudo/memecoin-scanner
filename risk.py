"""Reine Risiko-Bewertungslogik für Solana-Token.

Nimmt bereits abgerufene Daten entgegen (Mint-/Freeze-Authority, Holder-
Konzentration, Liquidität/Handelsdaten) und macht selbst keine API-Calls -
dadurch offline testbar, analog zu filters.py.

WICHTIG - das ist ein marktbasierter Heuristik-Check, kein vollständiger
Security-Audit:
- Mint-/Freeze-Authority und Token-2022-Extensions (TransferHook,
  PermanentDelegate, NonTransferable, Transfer-Steuer, veränderbare
  Token-Metadata) werden geprüft (siehe solana_rpc.py) - das deckt die
  gängigsten technischen Rug- und Honeypot-Signale ab, die sich direkt aus
  dem Mint-Account lesen lassen.
- Liquiditäts-Sperren/-Burns werden geprüft (evaluate_liquidity_lock, über
  GeckoTerminals locked_liquidity_percentage), aber nur sobald ein Coin
  einen klassischen LP-Token hat - für frische Coins auf einer Bonding
  Curve (z.B. pump.fun vor der Migration) liefert GeckoTerminal dafür noch
  keinen Wert, das wird bewusst nicht als Risiko gewertet.
- Die tatsächliche Ausführung der Transfer-Hook-Logik wird NICHT simuliert
  (nur ob die Extension aktiv ist) - eine echte Simulation bräuchte eine
  reale Swap-Transaktion samt Wallet und ist damit ein eigenes, größeres
  Feature.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from config import DEFAULT_RISK_THRESHOLDS, RiskThresholds


class Severity(IntEnum):
    NIEDRIG = 0
    MITTEL = 1
    HOCH = 2
    KRITISCH = 3


@dataclass(frozen=True)
class RiskFinding:
    severity: Severity
    message: str


@dataclass(frozen=True)
class RiskReport:
    address: str
    symbol: str
    overall: Severity
    findings: list[RiskFinding]
    unchecked: list[str]


def evaluate_authorities(
    mint_authority: str | None,
    freeze_authority: str | None,
) -> list[RiskFinding]:
    findings: list[RiskFinding] = []

    if mint_authority is not None:
        findings.append(RiskFinding(
            Severity.KRITISCH,
            "Mint-Authority aktiv - Ersteller kann jederzeit neue Token erzeugen und den Supply verwässern",
        ))

    if freeze_authority is not None:
        findings.append(RiskFinding(
            Severity.KRITISCH,
            "Freeze-Authority aktiv - Ersteller kann einzelne Wallets vom Verkauf ausschließen (Honeypot-Risiko)",
        ))

    return findings


def evaluate_token_extensions(
    extensions: frozenset[str],
    transfer_fee_basis_points: int | None,
    thresholds: RiskThresholds = DEFAULT_RISK_THRESHOLDS,
    update_authority: str | None = None,
) -> list[RiskFinding]:
    findings: list[RiskFinding] = []

    if "transferHook" in extensions:
        findings.append(RiskFinding(
            Severity.KRITISCH,
            "TransferHook-Extension aktiv - Ersteller kann bei jedem Transfer beliebige "
            "Logik ausführen (z.B. Verkäufe blockieren)",
        ))

    if "permanentDelegate" in extensions:
        findings.append(RiskFinding(
            Severity.KRITISCH,
            "PermanentDelegate-Extension aktiv - Ersteller kann Tokens aus jedem Wallet "
            "bewegen oder verbrennen, unabhängig vom Holder",
        ))

    if "nonTransferable" in extensions:
        findings.append(RiskFinding(
            Severity.KRITISCH,
            "NonTransferable-Extension aktiv - Token kann grundsätzlich nicht übertragen/verkauft werden",
        ))

    if "tokenMetadata" in extensions and update_authority is not None:
        findings.append(RiskFinding(
            Severity.MITTEL,
            "Token-Metadata noch veränderbar (updateAuthority gesetzt) - Ersteller kann Name/"
            "Symbol/Bild nachträglich ändern (Rebrand-Risiko: toter Coin als neuer Hype-Coin getarnt)",
        ))

    if transfer_fee_basis_points is not None:
        fee_percent = transfer_fee_basis_points / 100
        if transfer_fee_basis_points >= thresholds.transfer_fee_bps_high:
            findings.append(RiskFinding(
                Severity.HOCH, f"Sehr hohe Transfer-Steuer ({fee_percent:.1f}%) bei jedem Trade"
            ))
        elif transfer_fee_basis_points >= thresholds.transfer_fee_bps_medium:
            findings.append(RiskFinding(
                Severity.MITTEL, f"Erhöhte Transfer-Steuer ({fee_percent:.1f}%) bei jedem Trade"
            ))

    return findings


def evaluate_holder_concentration(
    top10_percent: float | None,
    holder_count: int | None,
    thresholds: RiskThresholds = DEFAULT_RISK_THRESHOLDS,
) -> list[RiskFinding]:
    findings: list[RiskFinding] = []

    if top10_percent is not None:
        if top10_percent >= thresholds.top10_percent_high:
            findings.append(RiskFinding(
                Severity.HOCH, f"Top-10-Holder besitzen {top10_percent:.0f}% des Supplys"
            ))
        elif top10_percent >= thresholds.top10_percent_medium:
            findings.append(RiskFinding(
                Severity.MITTEL, f"Top-10-Holder besitzen {top10_percent:.0f}% des Supplys"
            ))

    if holder_count is not None and holder_count < thresholds.min_holder_count:
        findings.append(RiskFinding(Severity.MITTEL, f"Nur {holder_count} Holder insgesamt"))

    return findings


def evaluate_liquidity_lock(
    locked_liquidity_percentage: float | None,
    thresholds: RiskThresholds = DEFAULT_RISK_THRESHOLDS,
) -> list[RiskFinding]:
    """locked_liquidity_percentage kommt von GeckoTerminal (nur in der Pool-
    Detail-Antwort enthalten, siehe geckoterminal_client.PoolListing) und ist
    None, wenn der Pool noch keinen klassischen LP-Token hat (z.B. eine
    Bonding-Curve vor der Migration) - dort ist die Liquidität strukturell
    im Programm gebunden und kann vom Ersteller nicht einfach abgezogen
    werden, deshalb wird None NICHT als Risiko gewertet, sondern schlicht
    übersprungen (wie andere fehlende Werte in diesem Modul)."""
    if locked_liquidity_percentage is None:
        return []

    if locked_liquidity_percentage < thresholds.locked_liquidity_percent_high:
        return [RiskFinding(
            Severity.HOCH,
            f"Nur {locked_liquidity_percentage:.0f}% der Liquidität gesperrt - "
            "hohes Rug-Pull-Risiko (Ersteller kann den Rest jederzeit abziehen)",
        )]

    if locked_liquidity_percentage < thresholds.locked_liquidity_percent_medium:
        return [RiskFinding(
            Severity.MITTEL,
            f"Nur {locked_liquidity_percentage:.0f}% der Liquidität gesperrt",
        )]

    return []


def evaluate_liquidity_and_trading(
    liquidity_usd: float,
    volume_24h_usd: float,
    trade_24h: int,
    unique_wallet_24h: int,
    thresholds: RiskThresholds = DEFAULT_RISK_THRESHOLDS,
) -> list[RiskFinding]:
    findings: list[RiskFinding] = []

    if liquidity_usd < thresholds.min_liquidity_usd:
        findings.append(RiskFinding(Severity.HOCH, f"Sehr geringe Liquidität (${liquidity_usd:,.0f})"))

    if liquidity_usd > 0 and volume_24h_usd / liquidity_usd > thresholds.max_volume_liquidity_ratio:
        ratio = volume_24h_usd / liquidity_usd
        findings.append(RiskFinding(
            Severity.MITTEL,
            f"Volumen/Liquidität-Verhältnis auffällig hoch ({ratio:.0f}x) - Preis leicht manipulierbar",
        ))

    if unique_wallet_24h > 0 and trade_24h / unique_wallet_24h > thresholds.max_trades_per_wallet:
        ratio = trade_24h / unique_wallet_24h
        findings.append(RiskFinding(
            Severity.MITTEL,
            f"Nur {unique_wallet_24h} Wallets für {trade_24h} Trades in 24h "
            f"({ratio:.0f} Trades/Wallet) - möglicher Wash-Trade-Hinweis",
        ))

    return findings


def evaluate_creator_history(
    previous_coin_count: int,
    thresholds: RiskThresholds = DEFAULT_RISK_THRESHOLDS,
    rugged_coin_count: int = 0,
) -> list[RiskFinding]:
    """previous_coin_count: Anzahl ANDERER Coins in unserer lokalen Historie
    mit derselben Creator-Wallet (Token-2022 updateAuthority, siehe
    solana_rpc.py) - reines Zähl-Signal ("Serial-Launcher").

    rugged_coin_count: davon, wie viele bei einem WIEDERHOLTEN Scan bereits
    einen Liquiditäts-Einbruch (siehe history.creator_rugged_coin_count)
    gezeigt haben - ein tatsächliches Outcome-Signal statt nur eine Zählung,
    und deutlich aussagekräftiger: ein Serial-Launcher mit durchweg stabilen
    früheren Coins ist ein anderes Risiko als einer mit einer Rug-Spur.
    """
    if rugged_coin_count > 0:
        return [RiskFinding(
            Severity.KRITISCH,
            f"Creator-Wallet hat bei {rugged_coin_count} von {previous_coin_count} früheren Coins "
            "bereits einen Liquiditäts-Einbruch verursacht (Rug-Pull-Muster)",
        )]

    if previous_coin_count >= thresholds.serial_launcher_count_high:
        return [RiskFinding(
            Severity.HOCH,
            f"Creator-Wallet hat bereits {previous_coin_count} weitere Coins in unserer "
            "Historie gelistet (Serial-Launcher-Muster)",
        )]

    if previous_coin_count >= thresholds.serial_launcher_count_medium:
        return [RiskFinding(
            Severity.MITTEL,
            f"Creator-Wallet hat bereits {previous_coin_count} weitere Coin(s) in unserer Historie",
        )]

    return []


def evaluate_liquidity_trend(
    current_liquidity_usd: float | None,
    previous_liquidity_usd: float | None,
    minutes_since_previous: float | None,
    thresholds: RiskThresholds = DEFAULT_RISK_THRESHOLDS,
) -> list[RiskFinding]:
    """Vergleicht die aktuelle Liquidität mit dem letzten gespeicherten Snapshot
    derselben Adresse (siehe history.py). Ohne vorherigen Snapshot (erster
    Scan dieser Adresse) gibt es keine Trend-Aussage.
    """
    if current_liquidity_usd is None or previous_liquidity_usd is None or previous_liquidity_usd <= 0:
        return []

    change_percent = (current_liquidity_usd - previous_liquidity_usd) / previous_liquidity_usd * 100
    minutes_text = f"{minutes_since_previous:.0f} Min" if minutes_since_previous is not None else "letztem Scan"

    if change_percent <= -thresholds.liquidity_crash_percent_high:
        return [RiskFinding(
            Severity.HOCH,
            f"Liquidität um {abs(change_percent):.0f}% eingebrochen seit {minutes_text} - "
            "möglicher Rug in Gange",
        )]

    if change_percent <= -thresholds.liquidity_crash_percent_medium:
        return [RiskFinding(
            Severity.MITTEL,
            f"Liquidität um {abs(change_percent):.0f}% gesunken seit {minutes_text}",
        )]

    return []


def build_report(
    address: str,
    symbol: str,
    findings: list[RiskFinding],
    unchecked: list[str],
) -> RiskReport:
    overall = max((f.severity for f in findings), default=Severity.NIEDRIG)
    return RiskReport(address=address, symbol=symbol, overall=overall, findings=findings, unchecked=unchecked)
