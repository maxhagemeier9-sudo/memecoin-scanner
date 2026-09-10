"""Einzelner Scan-Zyklus für zeitgesteuerte Trigger (z.B. GitHub Actions
Cron) statt eines Dauerlaufs wie monitor.py - läuft einmal durch und
beendet sich danach.

Nutzt dieselbe history.sqlite3 wie monitor.py/risk_scan.py. Die
Alert-Deduplizierung läuft über die Historie-DB (history.has_been_alerted),
funktioniert also korrekt auch über mehrere unabhängige Prozess-Starts
hinweg - wichtig, weil jeder GitHub-Actions-Trigger in einem komplett
frischen Container läuft (siehe .github/workflows/scan.yml, das die DB
per actions/cache zwischen Läufen wiederherstellt).
"""
from __future__ import annotations

import history
from birdeye_client import BirdeyeAPIError, BirdeyeClient
from monitor import scan_once


def main() -> None:
    client = BirdeyeClient()
    conn = history.connect()
    try:
        scan_once(client, conn)
    except BirdeyeAPIError as exc:
        print(f"Scan fehlgeschlagen: {exc}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
