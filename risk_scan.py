"""Risk-Scanner: bewertet neu gelistete Solana-Coins auf offensichtliche
Betrugs-/Gefahrensignale und verdichtet alles zu einem 0-100-Score.

Datenquellen seit 2026-09-10 (Birdeye-Compute-Units-Kontingent bis
2026-10-08 erschöpft, siehe birdeye_client.py):
- GeckoTerminal/CoinGecko On-Chain API (geckoterminal_client.py, keyless,
  kostenlos): Discovery neuer Pools sowie Preis/Liquidität/Volumen/
  Handelszahlen - alles in EINEM Call pro Discovery-Seite (20 Coins), statt
  vorher 3 Birdeye-Calls PRO Coin.
- Helius-RPC (solana_rpc.py, config.SOLANA_RPC_URL): Mint-/Freeze-Authority,
  Token-2022-Extensions, und Holder-Konzentration (aus
  getTokenLargestAccounts + Supply, da GeckoTerminals Holder-Endpunkte im
  Gratis-Tier gesperrt sind - live verifiziert, HTTP 401/429).
- Lokale Historie (history.py): Liquiditäts-Trend seit dem letzten Scan
  derselben Adresse und wie viele andere Coins dieselbe Creator-Wallet
  bereits gelistet hat ("Serial-Launcher"-Signal).

WICHTIG: holder_count (Gesamtzahl aller Holder, nicht nur Top-10) ist seit
dem Wechsel nicht mehr verfügbar - siehe config.ScoreWeights-Docstring.

Siehe risk.py für die genauen Risk-Kriterien und deren bewusste Grenzen
(u.a. KEINE Prüfung von Liquiditäts-Sperren/-Burns oder Contract-Logik) und
score.py für die Herleitung des Scores aus denselben Rohdaten.
"""
from __future__ import annotations

import html
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone

import history
from config import (
    DEFAULT_RISK_THRESHOLDS,
    EXPORT_RESULTS,
    RANKING_SCAN_PAGES,
    RANKING_TOP_N,
    RISING_STAR_RECHECK_LIMIT,
    RISK_SCAN_THROTTLE_SECONDS,
    RiskThresholds,
)
from export import export_ranking
from geckoterminal_client import GeckoTerminalAPIError, GeckoTerminalClient, PoolListing, parse_pool
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
from solana_rpc import SolanaRPCError, get_mint_authorities, get_top10_concentration


@dataclass(frozen=True)
class TokenAssessment:
    report: RiskReport
    score: Score
    liquidity_usd: float | None
    price: float | None = None
    pool_address: str | None = None


def assess_listing(listing: PoolListing, conn: sqlite3.Connection | None = None) -> TokenAssessment:
    """Bewertet einen bereits von GeckoTerminal abgerufenen Pool-Eintrag.
    Braucht nur noch 2 zusätzliche RPC-Calls (Authorities+Supply, danach
    Top10-Konzentration) - Preis/Liquidität/Volumen/Handelszahlen stecken
    schon in `listing`.
    """
    findings = []
    unchecked = []

    top10_percent = None
    transfer_fee_bps = None
    update_authority = None

    try:
        authorities = get_mint_authorities(listing.token_address)
        findings += evaluate_authorities(authorities.mint_authority, authorities.freeze_authority)
        findings += evaluate_token_extensions(authorities.extensions, authorities.transfer_fee_basis_points)
        transfer_fee_bps = authorities.transfer_fee_basis_points
        update_authority = authorities.update_authority

        try:
            top10_percent = get_top10_concentration(listing.token_address, authorities.total_supply)
            findings += evaluate_holder_concentration(top10_percent, None)
        except SolanaRPCError as exc:
            unchecked.append(f"Holder-Konzentration nicht prüfbar ({exc})")
    except SolanaRPCError as exc:
        unchecked.append(f"Mint-/Freeze-Authority/Extensions/Holder-Konzentration nicht prüfbar ({exc})")

    liquidity_usd = listing.liquidity_usd or 0
    volume_24h_usd = listing.volume_24h_usd or 0
    findings += evaluate_liquidity_and_trading(
        liquidity_usd=liquidity_usd,
        volume_24h_usd=volume_24h_usd,
        trade_24h=listing.trade_24h,
        unique_wallet_24h=listing.unique_wallet_24h,
    )

    if conn is not None:
        previous = history.previous_snapshot(conn, listing.token_address)
        if previous is not None:
            previous_time = datetime.fromisoformat(previous.scanned_at)
            minutes_since_previous = (datetime.now(timezone.utc) - previous_time).total_seconds() / 60
            findings += evaluate_liquidity_trend(liquidity_usd, previous.liquidity_usd, minutes_since_previous)

        if update_authority is not None:
            creator_coins = history.creator_history(conn, update_authority)
            previous_coin_count = len({s.address for s in creator_coins if s.address != listing.token_address})
            findings += evaluate_creator_history(previous_coin_count)

    report = build_report(listing.token_address, listing.symbol, findings, unchecked)

    score = compute_score(
        top10_percent=top10_percent,
        holder_count=None,
        liquidity_usd=liquidity_usd,
        volume_24h_usd=volume_24h_usd,
        trade_24h=listing.trade_24h,
        unique_wallet_24h=listing.unique_wallet_24h,
        transfer_fee_basis_points=transfer_fee_bps,
        has_critical_finding=report.overall == Severity.KRITISCH,
    )

    if conn is not None:
        history.record_snapshot(conn, history.Snapshot(
            address=listing.token_address,
            symbol=listing.symbol,
            scanned_at=datetime.now(timezone.utc).isoformat(),
            liquidity_usd=liquidity_usd,
            volume_24h_usd=volume_24h_usd,
            holder_count=None,
            top10_percent=top10_percent,
            score_total=score.total,
            risk_level=report.overall.name,
            creator_authority=update_authority,
            price=listing.price_usd,
            pool_address=listing.pool_address,
        ))

    return TokenAssessment(
        report=report, score=score, liquidity_usd=liquidity_usd,
        price=listing.price_usd, pool_address=listing.pool_address,
    )


def scan_new_coins(
    client: GeckoTerminalClient,
    pages: int = RANKING_SCAN_PAGES,
    conn: sqlite3.Connection | None = None,
) -> list[tuple[dict, TokenAssessment]]:
    """Gibt (listing_dict, TokenAssessment)-Paare zurück. `listing_dict` hat
    dieselben Keys wie vorher ("address", "symbol", "liquidityAddedAt"),
    damit _print_ranking/format_telegram_summary/monitor.py unverändert
    bleiben. Dedupliziert nach Token-Adresse, falls derselbe Coin über
    mehrere Pools/Seiten auftaucht.
    """
    pools = []
    for page in range(1, pages + 1):
        pools += client.get_new_pools(page=page)
        client.throttle()

    results = []
    seen_tokens: set[str] = set()

    for pool in pools:
        listing = parse_pool(pool)
        if listing is None or listing.token_address in seen_tokens:
            continue
        seen_tokens.add(listing.token_address)

        assessment = assess_listing(listing, conn=conn)
        listing_dict = {
            "address": listing.token_address,
            "symbol": listing.symbol,
            "liquidityAddedAt": listing.created_at,
        }
        results.append((listing_dict, assessment))
        time.sleep(RISK_SCAN_THROTTLE_SECONDS)

    return results


def is_rising(
    first: history.Snapshot,
    assessment: TokenAssessment,
    thresholds: RiskThresholds = DEFAULT_RISK_THRESHOLDS,
) -> bool:
    """Ein bereits bekannter Coin gilt als "Rising", wenn er inzwischen
    höchstens MITTEL-Risiko hat UND seine Liquidität seit der allerersten
    Sichtung um mindestens `rising_star_min_liquidity_growth` gewachsen ist.
    Das sind typischerweise Coins, die noch früh genug sind, aber schon genug
    Substanz haben, um z.B. auch in kuratierten Trading-Apps aufzutauchen.
    """
    if assessment.report.overall not in (Severity.NIEDRIG, Severity.MITTEL):
        return False

    if first.liquidity_usd is None or assessment.liquidity_usd is None or first.liquidity_usd <= 0:
        return False

    growth = (assessment.liquidity_usd - first.liquidity_usd) / first.liquidity_usd
    return growth >= thresholds.rising_star_min_liquidity_growth


def recheck_watchlist(
    client: GeckoTerminalClient,
    conn: sqlite3.Connection,
    limit: int = RISING_STAR_RECHECK_LIMIT,
) -> list[tuple[history.Snapshot, TokenAssessment]]:
    """Prüft bereits bekannte, bisher unauffällige Coins (NIEDRIG/MITTEL-Risiko,
    mind. 2 Snapshots) erneut per gespeicherter Pool-Adresse nach -
    unabhängig davon, ob sie noch in den neuesten new_pools-Einträgen
    auftauchen (die fallen nach ca. 10-20 Min aus diesem Fenster raus, siehe
    scan_new_coins). Liefert (erster Snapshot, aktuelle Bewertung) je
    Kandidat, Grundlage für "Rising Coin"-Alerts (siehe is_rising,
    monitor.scan_once). Kandidaten ohne gespeicherte Pool-Adresse (ältere
    Snapshots von vor diesem Feld) werden übersprungen.
    """
    candidates = history.watchlist_candidates(conn, limit=limit)
    results = []

    for candidate in candidates:
        if not candidate.pool_address:
            continue

        try:
            pool = client.get_pool(candidate.pool_address)
        except GeckoTerminalAPIError:
            continue
        if pool is None:
            continue

        listing = parse_pool(pool)
        if listing is None:
            continue

        first = history.first_snapshot(conn, candidate.address)
        if first is None:
            continue

        assessment = assess_listing(listing, conn=conn)
        results.append((first, assessment))
        client.throttle()

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
    Symbol wird HTML-escaped, weil Coin-Namen/Symbole aus externen Quellen
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
    client = GeckoTerminalClient()
    conn = history.connect()
    try:
        results = scan_new_coins(client, conn=conn)
    except GeckoTerminalAPIError as exc:
        print(f"Abruf fehlgeschlagen: {exc}")
        return
    finally:
        conn.close()

    _print_ranking(results)

    if EXPORT_RESULTS:
        export_ranking(rank_by_score(results))


if __name__ == "__main__":
    main()
