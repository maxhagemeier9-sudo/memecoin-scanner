"""Risk-Scanner: bewertet neu gelistete Solana-Coins auf offensichtliche
Betrugs-/Gefahrensignale und verdichtet alles zu einem 0-100-Score.

Kombiniert:
- Mint-/Freeze-Authority und Token-2022-Extensions (TransferHook,
  PermanentDelegate, NonTransferable, Transfer-Steuer) direkt per
  Solana-RPC (solana_rpc.py) - der Birdeye-Security-Endpoint dafür ist mit
  dem aktuellen Plan gesperrt (401).
- Holder-Konzentration und Liquiditäts-/Handelsdaten von Birdeye.
- Lokale Historie (history.py): Liquiditäts-Trend seit dem letzten Scan
  derselben Adresse und wie viele andere Coins dieselbe Creator-Wallet
  bereits gelistet hat ("Serial-Launcher"-Signal).

Siehe risk.py für die genauen Risk-Kriterien und deren bewusste Grenzen
(u.a. KEINE Prüfung von Liquiditäts-Sperren/-Burns oder Contract-Logik) und
score.py für die Herleitung des Scores aus denselben Rohdaten.
"""
from __future__ import annotations

import html
import sqlite3
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone

import history
from birdeye_client import BirdeyeAPIError, BirdeyeClient
from config import (
    EXPORT_RESULTS,
    RANKING_SCAN_LIMIT,
    RANKING_SCAN_PAGES,
    RANKING_TOP_N,
    RISK_SCAN_THROTTLE_SECONDS,
)
from export import export_ranking
from new_coins import age_minutes, format_age
from risk import (
    RiskReport,
    Severity,
    build_report,
    evaluate_authorities,
    evaluate_creator_history,
    evaluate_holder_concentration,
    evaluate_liquidity_and_trading,
    evaluate_liquidity_trend,
    evaluate_token_extensions,
)
from score import Score, compute_score
from solana_rpc import SolanaRPCError, get_mint_authorities


@dataclass(frozen=True)
class TokenAssessment:
    report: RiskReport
    score: Score
    liquidity_usd: float | None


def assess_token(
    client: BirdeyeClient,
    address: str,
    symbol: str,
    conn: sqlite3.Connection | None = None,
) -> TokenAssessment:
    findings = []
    unchecked = []

    top10_percent = None
    holder_count = None
    liquidity_usd = None
    volume_24h_usd = None
    trade_24h = None
    unique_wallet_24h = None
    transfer_fee_bps = None
    update_authority = None

    # Solana-RPC ist ein komplett anderer Dienst mit eigenem Rate-Limit als
    # Birdeye - läuft parallel im Hintergrund, während wir sequenziell die
    # gedrosselten Birdeye-Calls machen, statt seine Latenz on top draufzuschlagen.
    with ThreadPoolExecutor(max_workers=1) as executor:
        authorities_future = executor.submit(get_mint_authorities, address)

        try:
            holder_data = client.get_holders(address, limit=1)
            top10_percent = holder_data.get("top10_hold_percent")
            holder_count = holder_data.get("holder")
            findings += evaluate_holder_concentration(top10_percent, holder_count)
        except BirdeyeAPIError as exc:
            unchecked.append(f"Holder-Konzentration nicht prüfbar ({exc})")

        time.sleep(RISK_SCAN_THROTTLE_SECONDS)

        try:
            market_data = client.get_market_data(address)
            time.sleep(RISK_SCAN_THROTTLE_SECONDS)
            trade_data = client.get_trade_data(address)
            liquidity_usd = market_data.get("liquidity") or 0
            volume_24h_usd = trade_data.get("volume_24h_usd") or 0
            trade_24h = trade_data.get("trade_24h") or 0
            unique_wallet_24h = trade_data.get("unique_wallet_24h") or 0
            findings += evaluate_liquidity_and_trading(
                liquidity_usd=liquidity_usd,
                volume_24h_usd=volume_24h_usd,
                trade_24h=trade_24h,
                unique_wallet_24h=unique_wallet_24h,
            )
        except BirdeyeAPIError as exc:
            unchecked.append(f"Liquiditäts-/Handelsdaten nicht prüfbar ({exc})")

        try:
            authorities = authorities_future.result()
            findings += evaluate_authorities(authorities.mint_authority, authorities.freeze_authority)
            findings += evaluate_token_extensions(authorities.extensions, authorities.transfer_fee_basis_points)
            transfer_fee_bps = authorities.transfer_fee_basis_points
            update_authority = authorities.update_authority
        except SolanaRPCError as exc:
            unchecked.append(f"Mint-/Freeze-Authority/Extensions nicht prüfbar ({exc})")

    if conn is not None:
        previous = history.previous_snapshot(conn, address)
        if previous is not None:
            previous_time = datetime.fromisoformat(previous.scanned_at)
            minutes_since_previous = (datetime.now(timezone.utc) - previous_time).total_seconds() / 60
            findings += evaluate_liquidity_trend(liquidity_usd, previous.liquidity_usd, minutes_since_previous)

        if update_authority is not None:
            creator_coins = history.creator_history(conn, update_authority)
            previous_coin_count = len({s.address for s in creator_coins if s.address != address})
            findings += evaluate_creator_history(previous_coin_count)

    report = build_report(address, symbol, findings, unchecked)

    score = compute_score(
        top10_percent=top10_percent,
        holder_count=holder_count,
        liquidity_usd=liquidity_usd,
        volume_24h_usd=volume_24h_usd,
        trade_24h=trade_24h,
        unique_wallet_24h=unique_wallet_24h,
        transfer_fee_basis_points=transfer_fee_bps,
        has_critical_finding=report.overall == Severity.KRITISCH,
    )

    if conn is not None:
        history.record_snapshot(conn, history.Snapshot(
            address=address,
            symbol=symbol,
            scanned_at=datetime.now(timezone.utc).isoformat(),
            liquidity_usd=liquidity_usd,
            volume_24h_usd=volume_24h_usd,
            holder_count=holder_count,
            top10_percent=top10_percent,
            score_total=score.total,
            risk_level=report.overall.name,
            creator_authority=update_authority,
        ))

    return TokenAssessment(report=report, score=score, liquidity_usd=liquidity_usd)


def _listing_label(listing: dict) -> str:
    """Symbol, sonst Name, sonst "???" - Birdeye liefert für ganz frisch
    indizierte Coins teils beides als null zurück (echte Datenlücke, kein Bug)."""
    return listing.get("symbol") or listing.get("name") or "???"


def scan_new_coins(
    client: BirdeyeClient,
    limit: int = RANKING_SCAN_LIMIT,
    pages: int = RANKING_SCAN_PAGES,
    conn: sqlite3.Connection | None = None,
) -> list[tuple[dict, TokenAssessment]]:
    listings = []
    for page in range(pages):
        listings += client.get_new_listings(limit=limit, offset=page * limit)
        if page < pages - 1:
            time.sleep(RISK_SCAN_THROTTLE_SECONDS)

    results = []
    for listing in listings:
        assessment = assess_token(client, listing["address"], _listing_label(listing), conn=conn)
        results.append((listing, assessment))
        time.sleep(RISK_SCAN_THROTTLE_SECONDS)

    return results


def _score_icon(total: int) -> str:
    if total >= 70:
        return "🟢"
    if total >= 40:
        return "🟡"
    if total >= 15:
        return "🟠"
    return "🔴"


def rank_by_score(
    results: list[tuple[dict, TokenAssessment]],
    top_n: int = RANKING_TOP_N,
) -> list[tuple[dict, TokenAssessment]]:
    return sorted(results, key=lambda pair: pair[1].score.total, reverse=True)[:top_n]


def _display_width(text: str) -> int:
    """Terminal-Breite eines Strings: CJK/Fullwidth-Zeichen (z.B. 牛来) belegen
    zwei Spalten, `str.ljust`/f-string-Padding zählt aber nur Zeichen und
    verschiebt dadurch Tabellenspalten bei solchen Symbolen."""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


def _top_finding_text(report: RiskReport, max_len: int = 42) -> str:
    if not report.findings:
        return "keine Auffälligkeiten"

    worst = max(report.findings, key=lambda f: f.severity)
    text = worst.message
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def format_telegram_summary(
    results: list[tuple[dict, TokenAssessment]],
    top_n: int = RANKING_TOP_N,
) -> str:
    """Nachrichtentext für Telegram (HTML-parse_mode) - eine Zusammenfassung
    pro abgeschlossenem Scan, inkl. Adresse pro Coin zum direkten Kopieren.
    Symbol wird HTML-escaped, weil Coin-Namen/Symbole aus der Birdeye-API
    kommen und beliebige Zeichen enthalten können (z.B. "&", "<") - ohne
    Escaping würde das die Telegram-HTML-Parsing brechen oder Markup injizieren.
    """
    ranked = rank_by_score(results, top_n)

    lines = [f"📊 Scan abgeschlossen – Top {len(ranked)} von {len(results)} Coins"]

    for rank, (listing, assessment) in enumerate(ranked, start=1):
        report, score = assessment.report, assessment.score
        icon = _score_icon(score.total)
        cap_note = " (gedeckelt)" if score.capped else ""
        symbol = html.escape(report.symbol)

        lines.append(
            f"\n{icon} {rank}. {symbol} — Score {score.total}/100{cap_note} — {report.overall.name}\n"
            f"<code>{report.address}</code>"
        )

    return "\n".join(lines)


def _print_ranking(results: list[tuple[dict, TokenAssessment]], top_n: int = RANKING_TOP_N) -> None:
    ranked = rank_by_score(results, top_n)

    print(f"Top {len(ranked)} von {len(results)} gescannten Coins (nach Score):\n")

    header = (
        f"   {'#':>2} | {'Symbol':10} | {'Score':>6} | {'Risiko':8} | "
        f"{'Alter':>6} | {'Liquidität':>11} | Top-Finding"
    )
    print(header)
    print("-" * len(header))

    capped_count = 0
    for rank, (listing, assessment) in enumerate(ranked, start=1):
        report, score = assessment.report, assessment.score
        age = format_age(age_minutes(listing["liquidityAddedAt"]))
        icon = _score_icon(score.total)
        cap_marker = "*" if score.capped else " "
        capped_count += 1 if score.capped else 0

        liquidity_text = f"${assessment.liquidity_usd:,.0f}" if assessment.liquidity_usd is not None else "?"
        score_text = f"{score.total}{cap_marker}"

        print(
            f"{icon} {rank:>2} | {_pad(report.symbol[:10], 10)} | {score_text:>6} | "
            f"{report.overall.name:8} | {age:>6} | {liquidity_text:>11} | {_top_finding_text(report)}"
        )

    if capped_count:
        print(f"\n* Score gedeckelt auf max. 10 wegen KRITISCH-Finding ({capped_count} von {len(ranked)})")


def main() -> None:
    client = BirdeyeClient()
    conn = history.connect()
    try:
        results = scan_new_coins(client, conn=conn)
    except BirdeyeAPIError as exc:
        print(f"Abruf fehlgeschlagen: {exc}")
        return
    finally:
        conn.close()

    _print_ranking(results)

    if EXPORT_RESULTS:
        export_ranking(rank_by_score(results))


if __name__ == "__main__":
    main()
