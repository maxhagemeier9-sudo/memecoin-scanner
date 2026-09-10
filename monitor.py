"""Dauerlauf-Modus: scannt in Intervallen neue Coins und alarmiert, sobald
ein Coin auffällig gut aussieht (siehe _should_alert) - ohne dieselbe Adresse
mehrfach zu melden. Beenden mit Strg+C.

Nutzt dieselbe Historie-DB wie risk_scan.py, dadurch profitieren auch die
Trend-/Creator-Signale (evaluate_liquidity_trend, evaluate_creator_history)
vom wiederholten Scannen über die Zeit.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from datetime import datetime, timezone

import history
from alerts import alert
from birdeye_client import BirdeyeAPIError, BirdeyeClient
from config import MONITOR_ALERT_SCORE_THRESHOLD, MONITOR_INTERVAL_SECONDS
from risk import Severity
from risk_scan import (
    TokenAssessment,
    _print_ranking,
    format_telegram_summary,
    is_rising,
    recheck_watchlist,
    scan_new_coins,
)
from telegram_alerts import TelegramError, send_telegram_message


def _should_alert(assessment: TokenAssessment) -> bool:
    """Alarmiert entweder bei hohem Score ODER bei höchstens mittlerem Risiko,
    unabhängig vom genauen Score. Reine Score>=70-Coins sind bei ganz frischen
    Listings selten, weil Holder-Konzentration direkt nach dem Launch fast
    immer schlecht aussieht (kaum jemand hat schon gekauft) - ein Coin ohne
    HOCH/KRITISCH-Finding ist trotzdem ein brauchbares Signal, auch mit
    Score < 70.
    """
    return assessment.score.total >= MONITOR_ALERT_SCORE_THRESHOLD or assessment.report.overall <= Severity.MITTEL


def scan_once(client: BirdeyeClient, conn: sqlite3.Connection) -> None:
    """Ein Scan-Zyklus: scannen, anzeigen, Zusammenfassung an Telegram
    schicken, neue auffällige Coins (siehe _should_alert) zusätzlich
    alarmieren, danach die Watchlist bereits bekannter Coins auf Wachstum
    prüfen ("Rising Coins", siehe risk_scan.recheck_watchlist/is_rising).
    Getrennt von der Endlosschleife, damit es isoliert testbar ist.

    Die Alert-Deduplizierung läuft über die Historie-DB (history.has_been_alerted/
    has_been_rising_alerted) statt über ein In-Memory-Set, damit sie auch über
    mehrere unabhängige Prozess-Starts hinweg funktioniert (z.B. ein frischer
    Prozess pro GitHub-Actions-Trigger statt eines Dauerlaufs).
    """
    results = scan_new_coins(client, conn=conn)
    _print_ranking(results)

    try:
        send_telegram_message(format_telegram_summary(results), parse_mode="HTML")
    except TelegramError as exc:
        print(f"   (Telegram-Zusammenfassung fehlgeschlagen: {exc})")

    for listing, assessment in results:
        address = listing["address"]
        if history.has_been_alerted(conn, address):
            continue
        if _should_alert(assessment):
            alert(
                f"{assessment.report.symbol} - Score {assessment.score.total}/100, "
                f"Risiko {assessment.report.overall.name} ({address})"
            )
            history.mark_alerted(conn, address)

    for first, assessment in recheck_watchlist(client, conn):
        address = assessment.report.address
        if history.has_been_rising_alerted(conn, address):
            continue
        if is_rising(first, assessment):
            growth_percent = (assessment.liquidity_usd - first.liquidity_usd) / first.liquidity_usd * 100
            alert(
                f"📈 Rising Coin: {assessment.report.symbol} - Liquidität +{growth_percent:.0f}% "
                f"seit Erstsichtung, jetzt Score {assessment.score.total}/100, "
                f"Risiko {assessment.report.overall.name} ({address})"
            )
            history.mark_rising_alerted(conn, address)


def run_monitor(client: BirdeyeClient, conn: sqlite3.Connection) -> None:
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
        except BirdeyeAPIError as exc:
            print(f"Scan fehlgeschlagen, versuche es beim nächsten Intervall erneut: {exc}")

        time.sleep(MONITOR_INTERVAL_SECONDS)


def main() -> None:
    client = BirdeyeClient()
    conn = history.connect()
    try:
        run_monitor(client, conn)
    except KeyboardInterrupt:
        print("\nMonitor beendet.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
