"""Dauerlauf-Modus: scannt in Intervallen neue Coins und alarmiert, sobald
ein Coin auffällig gut aussieht (siehe _should_alert) - ohne dieselbe Adresse
mehrfach zu melden. Beenden mit Strg+C.

Nutzt dieselbe Historie-DB wie risk_scan.py, dadurch profitieren auch die
Trend-/Creator-Signale (evaluate_liquidity_trend, evaluate_creator_history)
vom wiederholten Scannen über die Zeit. Pro Scan-Zyklus wird nicht für
jeden Alert eine Paper-Trading-Position eröffnet, sondern nur für den am
besten bewerteten Kandidaten (höchster Score) - so bleibt das Paper-Depot
fokussiert auf das jeweils stärkste Signal statt bei jedem Treffer zu
streuen. Alerts (Telegram) werden davon unabhängig weiterhin für jeden
qualifizierenden Coin verschickt.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from datetime import datetime, timezone

import history
import paper_trading
from alerts import alert
from config import MONITOR_ALERT_SCORE_THRESHOLD, MONITOR_INTERVAL_SECONDS
from geckoterminal_client import GeckoTerminalAPIError, GeckoTerminalClient
from risk import Severity
from risk_scan import (
    TokenAssessment,
    _print_ranking,
    format_telegram_summary,
    is_rising,
    pool_url,
    recheck_watchlist,
    scan_new_coins,
)
from telegram_alerts import TelegramError, send_telegram_message


def _maybe_open_paper_trade(conn: sqlite3.Connection, assessment: TokenAssessment) -> None:
    """Eröffnet eine simulierte Position, wenn ein Preis vorliegt und noch
    keine offene Position für diese Adresse existiert (siehe paper_trading.py)."""
    address = assessment.report.address
    if assessment.price is None or assessment.price <= 0:
        return
    if paper_trading.has_open_trade(conn, address):
        return
    paper_trading.open_trade(
        conn,
        address=address,
        symbol=assessment.report.symbol,
        entry_price=assessment.price,
        entry_score=assessment.score.total,
        entry_risk_level=assessment.report.overall.name,
    )


def _format_paper_trade_closures(closed: list[tuple]) -> str:
    """EINE kurze Zusammenfassungszeile für alle in diesem Scan-Zyklus
    geschlossenen Paper-Trades, unabhängig davon wie viele es sind - bewusst
    ohne Pro-Trade-Auflistung, damit die Nachricht bei vielen gleichzeitig
    fälligen Positionen (z.B. nach einem Backlog) kurz bleibt statt lang zu
    werden. Details stehen bei Bedarf im CI-Log bzw. paper_trading.py."""
    count = len(closed)
    pnl_percents = [(exit_price - trade.entry_price) / trade.entry_price * 100 for trade, _, exit_price in closed]
    wins = sum(1 for p in pnl_percents if p > 0)
    avg_pnl = sum(pnl_percents) / count
    return f"📝 Paper-Trading: {count} Position(en) geschlossen ({wins} Gewinn/{count - wins} Verlust), Ø {avg_pnl:+.0f}%"


def _with_link(text: str, pool_address: str | None) -> str:
    """Hängt den GeckoTerminal-Chart-Link an, sofern eine Pool-Adresse bekannt
    ist - Telegram macht aus einer nackten URL im Text automatisch einen
    klickbaren Link, auch ohne HTML-parse_mode (siehe alerts.alert)."""
    url = pool_url(pool_address)
    return f"{text}\n{url}" if url else text


def _should_alert(assessment: TokenAssessment) -> bool:
    """Alarmiert entweder bei hohem Score ODER bei höchstens mittlerem Risiko,
    unabhängig vom genauen Score. Reine Score>=70-Coins sind bei ganz frischen
    Listings selten, weil Holder-Konzentration direkt nach dem Launch fast
    immer schlecht aussieht (kaum jemand hat schon gekauft) - ein Coin ohne
    HOCH/KRITISCH-Finding ist trotzdem ein brauchbares Signal, auch mit
    Score < 70.
    """
    return assessment.score.total >= MONITOR_ALERT_SCORE_THRESHOLD or assessment.report.overall <= Severity.MITTEL


def scan_once(client: GeckoTerminalClient, conn: sqlite3.Connection) -> None:
    """Ein Scan-Zyklus: scannen, anzeigen, Zusammenfassung an Telegram
    schicken, neue auffällige Coins (siehe _should_alert) zusätzlich
    alarmieren, danach die Watchlist bereits bekannter Coins auf Wachstum
    prüfen ("Rising Coins", siehe risk_scan.recheck_watchlist/is_rising).
    Getrennt von der Endlosschleife, damit es isoliert testbar ist.

    Die Alert-Deduplizierung läuft über die Historie-DB (history.has_been_alerted/
    has_been_rising_alerted) statt über ein In-Memory-Set, damit sie auch über
    mehrere unabhängige Prozess-Starts hinweg funktioniert (z.B. ein frischer
    Prozess pro GitHub-Actions-Trigger statt eines Dauerlaufs).

    Alerts werden für jeden qualifizierenden Coin verschickt, aber nur der
    Kandidat mit dem höchsten Score bekommt pro Zyklus eine Paper-Trading-
    Position eröffnet (siehe _maybe_open_paper_trade).
    """
    paper_trading.ensure_schema(conn)

    results = scan_new_coins(client, conn=conn)
    _print_ranking(results)

    try:
        send_telegram_message(format_telegram_summary(results), parse_mode="HTML")
    except TelegramError as exc:
        print(f"   (Telegram-Zusammenfassung fehlgeschlagen: {exc})")

    best_candidate: TokenAssessment | None = None

    for listing, assessment in results:
        address = listing["address"]
        if history.has_been_alerted(conn, address):
            continue
        if _should_alert(assessment):
            alert(_with_link(
                f"{assessment.report.symbol} - Score {assessment.score.total}/100, "
                f"Risiko {assessment.report.overall.name} ({address})",
                assessment.pool_address,
            ))
            history.mark_alerted(conn, address)
            if best_candidate is None or assessment.score.total > best_candidate.score.total:
                best_candidate = assessment

    for first, assessment in recheck_watchlist(client, conn):
        address = assessment.report.address
        if history.has_been_rising_alerted(conn, address):
            continue
        if is_rising(first, assessment):
            growth_percent = (assessment.liquidity_usd - first.liquidity_usd) / first.liquidity_usd * 100
            alert(_with_link(
                f"📈 Rising Coin: {assessment.report.symbol} - Liquidität +{growth_percent:.0f}% "
                f"seit Erstsichtung, jetzt Score {assessment.score.total}/100, "
                f"Risiko {assessment.report.overall.name} ({address})",
                assessment.pool_address,
            ))
            history.mark_rising_alerted(conn, address)
            if best_candidate is None or assessment.score.total > best_candidate.score.total:
                best_candidate = assessment

    if best_candidate is not None:
        _maybe_open_paper_trade(conn, best_candidate)

    closed_paper_trades = paper_trading.process_open_trades(client, conn)
    if closed_paper_trades:
        for trade, reason, exit_price in closed_paper_trades:
            print(f"   Paper-Trade geschlossen: {trade.symbol} - {reason} ({trade.address})")
        try:
            send_telegram_message(_format_paper_trade_closures(closed_paper_trades))
        except TelegramError as exc:
            print(f"   (Paper-Trade-Telegram fehlgeschlagen: {exc})")

    paper_trading.maybe_send_daily_summary(conn)


def run_monitor(client: GeckoTerminalClient, conn: sqlite3.Connection) -> None:
    # Ohne Terminal (z.B. Hintergrundprozess) puffert Python stdout komplett -
    # ohne das hier kommt bei einem Dauerlauf keine Ausgabe an, bis der
    # Prozess von selbst endet. line_buffering sorgt dafür, dass jede Zeile
    # sofort rausgeht.
    sys.stdout.reconfigure(line_buffering=True)

    print(
        f"Monitor gestartet - Intervall {MONITOR_INTERVAL_SECONDS}s, "
        f"Alert bei Score >= {MONITOR_ALERT_SCORE_THRESHOLD} oder Risiko <= MITTEL"
    )
    print("Beenden mit Strg+C\n")

    while True:
        timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"--- Scan um {timestamp} UTC ---")

        try:
            scan_once(client, conn)
        except GeckoTerminalAPIError as exc:
            print(f"Scan fehlgeschlagen, versuche es beim nächsten Intervall erneut: {exc}")

        time.sleep(MONITOR_INTERVAL_SECONDS)


def main() -> None:
    client = GeckoTerminalClient()
    conn = history.connect()
    try:
        run_monitor(client, conn)
    except KeyboardInterrupt:
        print("\nMonitor beendet.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
