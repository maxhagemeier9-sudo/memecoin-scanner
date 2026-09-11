"""Backtesting: vergleicht den bei Erstsichtung vergebenen Score mit der
tatsächlichen Kursentwicklung (GeckoTerminal OHLCV) - beantwortet, ob unsere
Scoring-Logik überhaupt prädiktiv ist, bevor wir simuliertes (paper_trading.py)
oder gar echtes Geld darauf verwetten.

Nutzt ausschließlich Daten, die wir schon selbst gesammelt haben
(history.all_first_snapshots) plus die öffentliche GeckoTerminal-OHLCV-API -
kein zusätzlicher externer Datensatz nötig. Läuft nur für Snapshots, die
Preis UND Pool-Adresse erfasst haben (seit dem Wechsel auf GeckoTerminal;
OHLCV braucht die Pool-Adresse, nicht die Token-Adresse) - ältere Snapshots
von davor werden übersprungen.

WICHTIG: GeckoTerminal liefert Kerzen NEUESTE ZUERST (absteigend nach Zeit) -
der Exit-Punkt ist also candles[0], nicht candles[-1]. Wenn die Erstsichtung
jünger ist als `horizon_minutes`, gibt es noch keine Kerze am vollen Horizont
- backtest_snapshot liefert dann die Preisveränderung bis zur aktuellsten
verfügbaren Kerze (kürzerer, tatsächlicher Zeitraum statt des Zielhorizonts).
Das steht im Ergebnis (elapsed_minutes).

Zwei Wege, Backtests zu erzeugen:
- run_backtest(): testet ALLE bekannten Erstsichtungen auf einmal zurück,
  nichts wird gespeichert - für manuelle/lokale Auswertung (siehe main()).
- maybe_run_daily_backtest_batch(): läuft automatisch 1x/Tag aus
  monitor.scan_once(), testet nur reifgewordene (Horizont erreicht), noch
  nicht getestete Coins in kleinen Portionen (BACKTEST_DAILY_LIMIT) zurück
  und speichert die Ergebnisse dauerhaft in backtest_results - Grundlage
  für die Backtest-Auswertung im Dashboard (dashboard_export.py).
"""
from __future__ import annotations

import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

import history
from config import BACKTEST_DAILY_LIMIT, BACKTEST_HORIZON_MINUTES, BACKTEST_THROTTLE_SECONDS
from geckoterminal_client import GeckoTerminalAPIError, GeckoTerminalClient


@dataclass(frozen=True)
class BacktestResult:
    address: str
    symbol: str
    first_seen_at: str
    first_score: int
    first_risk_level: str
    entry_price: float
    exit_price: float
    return_percent: float
    elapsed_minutes: float


def _unix_time(iso_timestamp: str) -> int:
    return int(datetime.fromisoformat(iso_timestamp).timestamp())


def backtest_snapshot(
    client: GeckoTerminalClient,
    snapshot: history.Snapshot,
    horizon_minutes: int = BACKTEST_HORIZON_MINUTES,
) -> BacktestResult | None:
    """None, wenn kein Einstiegspreis/keine Pool-Adresse vorliegt oder keine
    OHLCV-Daten für den Zeitraum verfügbar sind (z.B. Coin ohne jeden
    weiteren Handel seitdem)."""
    if snapshot.price is None or snapshot.price <= 0 or not snapshot.pool_address:
        return None

    time_from = _unix_time(snapshot.scanned_at)
    before_timestamp = time_from + horizon_minutes * 60

    try:
        candles = client.get_ohlcv(
            snapshot.pool_address,
            timeframe="minute",
            aggregate=1,
            before_timestamp=before_timestamp,
            limit=min(horizon_minutes, 1000),
        )
    except GeckoTerminalAPIError:
        return None

    if not candles or len(candles[0]) < 6:
        return None

    # neueste zuerst -> die erste Kerze ist die naeheste am Ziel-Horizont
    exit_unix_time, _open, _high, _low, exit_price, _volume = candles[0][:6]

    elapsed_minutes = (exit_unix_time - time_from) / 60
    return_percent = (exit_price - snapshot.price) / snapshot.price * 100

    return BacktestResult(
        address=snapshot.address,
        symbol=snapshot.symbol,
        first_seen_at=snapshot.scanned_at,
        first_score=snapshot.score_total,
        first_risk_level=snapshot.risk_level,
        entry_price=snapshot.price,
        exit_price=exit_price,
        return_percent=return_percent,
        elapsed_minutes=elapsed_minutes,
    )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS backtest_results (
    address TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    first_score INTEGER NOT NULL,
    first_risk_level TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    return_percent REAL NOT NULL,
    elapsed_minutes REAL NOT NULL,
    backtested_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backtest_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_RESULT_COLUMNS = (
    "address, symbol, first_seen_at, first_score, first_risk_level, "
    "entry_price, exit_price, return_percent, elapsed_minutes"
)

_DAILY_BACKTEST_META_KEY = "last_daily_backtest_date"
# Andere Stunde als der Paper-Trading-Tagesreport (siehe paper_trading.py),
# damit sich beide taeglichen Batch-Jobs nicht denselben Scan-Zyklus teilen.
_DAILY_BACKTEST_HOUR_UTC = 9


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


def _get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM backtest_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO backtest_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def _store_result(conn: sqlite3.Connection, result: BacktestResult) -> None:
    conn.execute(
        f"""INSERT OR IGNORE INTO backtest_results ({_RESULT_COLUMNS}, backtested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            result.address, result.symbol, result.first_seen_at, result.first_score,
            result.first_risk_level, result.entry_price, result.exit_price,
            result.return_percent, result.elapsed_minutes,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


def stored_results(conn: sqlite3.Connection) -> list[BacktestResult]:
    """Alle bisher dauerhaft gespeicherten Backtest-Ergebnisse (siehe
    maybe_run_daily_backtest_batch) - im Gegensatz zu run_backtest() kein
    erneuter API-Call, liest nur, was bereits berechnet wurde."""
    rows = conn.execute(f"SELECT {_RESULT_COLUMNS} FROM backtest_results").fetchall()
    return [BacktestResult(*row) for row in rows]


def run_daily_backtest_batch(
    client: GeckoTerminalClient,
    conn: sqlite3.Connection,
    horizon_minutes: int = BACKTEST_HORIZON_MINUTES,
    limit: int = BACKTEST_DAILY_LIMIT,
    now: datetime | None = None,
) -> list[BacktestResult]:
    """Testet bis zu `limit` NOCH NICHT zurückgetestete Coins zurück, deren
    Erstsichtung mindestens `horizon_minutes` zurückliegt (der Zielhorizont
    also tatsächlich erreicht ist), und speichert die Ergebnisse dauerhaft
    (siehe _store_result) - im Gegensatz zu run_backtest() nicht "alles auf
    einmal", sondern häppchenweise über mehrere Tage, um GeckoTerminal-OHLCV-
    Calls zu begrenzen."""
    ensure_schema(conn)
    reference_now = now or datetime.now(timezone.utc)
    already_done = {r.address for r in stored_results(conn)}

    candidates = []
    for snapshot in history.all_first_snapshots(conn):
        if snapshot.address in already_done or snapshot.price is None or not snapshot.pool_address:
            continue
        first_time = datetime.fromisoformat(snapshot.scanned_at)
        if (reference_now - first_time).total_seconds() / 60 < horizon_minutes:
            continue
        candidates.append(snapshot)
        if len(candidates) >= limit:
            break

    results = []
    for snapshot in candidates:
        result = backtest_snapshot(client, snapshot, horizon_minutes)
        if result is not None:
            _store_result(conn, result)
            results.append(result)
        client.throttle()

    return results


def maybe_run_daily_backtest_batch(
    client: GeckoTerminalClient,
    conn: sqlite3.Connection,
    now: datetime | None = None,
    limit: int = BACKTEST_DAILY_LIMIT,
) -> list[BacktestResult]:
    """Läuft höchstens einmal pro UTC-Kalendertag, zusätzlich auf ein festes
    Stunden-Fenster begrenzt (_DAILY_BACKTEST_HOUR_UTC) - analog zu
    paper_trading.maybe_send_daily_summary, damit nicht bei jedem 10-Minuten-
    Scan neu geprüft wird."""
    ensure_schema(conn)
    reference_now = now or datetime.now(timezone.utc)
    if reference_now.hour != _DAILY_BACKTEST_HOUR_UTC:
        return []

    today = reference_now.date().isoformat()
    if _get_meta(conn, _DAILY_BACKTEST_META_KEY) == today:
        return []

    results = run_daily_backtest_batch(client, conn, limit=limit, now=reference_now)
    _set_meta(conn, _DAILY_BACKTEST_META_KEY, today)
    return results


def run_backtest(
    client: GeckoTerminalClient,
    conn,
    horizon_minutes: int = BACKTEST_HORIZON_MINUTES,
    limit: int | None = None,
) -> list[BacktestResult]:
    snapshots = [s for s in history.all_first_snapshots(conn) if s.price is not None and s.pool_address]
    if limit is not None:
        snapshots = snapshots[:limit]

    results = []
    for snapshot in snapshots:
        result = backtest_snapshot(client, snapshot, horizon_minutes)
        if result is not None:
            results.append(result)
        time.sleep(BACKTEST_THROTTLE_SECONDS)

    return results


def summarize_by_risk_level(results: list[BacktestResult]) -> dict[str, dict]:
    """Durchschnittliche Rendite je Risiko-Level bei Erstsichtung - zeigt, ob
    unser Scoring tatsächlich mit besseren Ergebnissen korreliert (niedrigeres
    Risiko sollte im Schnitt nicht schlechter abschneiden als hohes)."""
    buckets: dict[str, list[float]] = defaultdict(list)
    for r in results:
        buckets[r.first_risk_level].append(r.return_percent)

    summary = {}
    for level, returns in buckets.items():
        summary[level] = {
            "count": len(returns),
            "avg_return_percent": sum(returns) / len(returns),
            "min_return_percent": min(returns),
            "max_return_percent": max(returns),
        }
    return summary


def _print_summary(results: list[BacktestResult]) -> None:
    if not results:
        print(
            "Keine Backtest-Ergebnisse (noch keine Snapshots mit erfasstem "
            "Preis+Pool-Adresse, oder keine OHLCV-Daten verfügbar)."
        )
        return

    print(f"{len(results)} Coins zurückgetestet:\n")
    summary = summarize_by_risk_level(results)
    for level in ("NIEDRIG", "MITTEL", "HOCH", "KRITISCH"):
        if level not in summary:
            continue
        s = summary[level]
        print(
            f"{level:9} | n={s['count']:3} | Ø {s['avg_return_percent']:+7.1f}% | "
            f"min {s['min_return_percent']:+7.1f}% | max {s['max_return_percent']:+7.1f}%"
        )

    print("\nEinzelne Coins:")
    for r in sorted(results, key=lambda r: r.return_percent, reverse=True):
        print(
            f"  {r.symbol:10} | Score {r.first_score:3} ({r.first_risk_level:8}) | "
            f"{r.return_percent:+7.1f}% über {r.elapsed_minutes:.0f} Min | {r.address}"
        )


def main() -> None:
    client = GeckoTerminalClient()
    conn = history.connect()
    try:
        results = run_backtest(client, conn)
    finally:
        conn.close()

    _print_summary(results)


if __name__ == "__main__":
    main()
