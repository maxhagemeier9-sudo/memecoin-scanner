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
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

import history
from config import BACKTEST_HORIZON_MINUTES, BACKTEST_THROTTLE_SECONDS
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
