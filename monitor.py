"""Dauerlauf-Modus: scannt in Intervallen neue Coins und alarmiert, sobald
ein Coin die Score-Schwelle überschreitet - ohne dieselbe Adresse mehrfach
zu melden. Beenden mit Strg+C.

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
from risk_scan import _print_ranking, scan_new_coins


def scan_once(client: BirdeyeClient, conn: sqlite3.Connection, alerted_addresses: set[str]) -> None:
    """Ein Scan-Zyklus: scannen, anzeigen, neue Score-Schwellen-Überschreitungen
    alarmieren. Getrennt von der Endlosschleife, damit es isoliert testbar ist.
    """
    results = scan_new_coins(client, conn=conn)
    _print_ranking(results)

    for listing, assessment in results:
        address = listing["address"]
        if address in alerted_addresses:
            continue
        if assessment.score.total >= MONITOR_ALERT_SCORE_THRESHOLD:
            alert(f"{assessment.report.symbol} erreicht Score {assessment.score.total}/100 ({address})")
            alerted_addresses.add(address)


def run_monitor(client: BirdeyeClient, conn: sqlite3.Connection) -> None:
    # Ohne Terminal (z.B. Hintergrundprozess) puffert Python stdout komplett -
    # ohne das hier kommt bei einem Dauerlauf keine Ausgabe an, bis der
    # Prozess von selbst endet. line_buffering sorgt dafür, dass jede Zeile
    # sofort rausgeht.
    sys.stdout.reconfigure(line_buffering=True)

    alerted_addresses: set[str] = set()

    print(
        f"Monitor gestartet - Intervall {MONITOR_INTERVAL_SECONDS}s, "
        f"Alert-Schwelle Score >= {MONITOR_ALERT_SCORE_THRESHOLD}"
    )
    print("Beenden mit Strg+C\n")

    while True:
        timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"--- Scan um {timestamp} UTC ---")

        try:
            scan_once(client, conn, alerted_addresses)
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
