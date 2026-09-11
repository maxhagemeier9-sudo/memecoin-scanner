"""Exportiert einen Snapshot aller Scanner-Daten (Historie + Paper Trading)
als JSON für das GitHub-Pages-Dashboard (docs/index.html liest docs/data.json).

Läuft als letzter Schritt in der CI (.github/workflows/scan.yml) nach jedem
Scan - die Datei wird direkt danach committed/gepusht, damit das Dashboard
spätestens ~1 Minute nach Scan-Ende aktuell ist. Rein lesend (keine API-
Calls), macht also nichts kaputt, falls es fehlschlägt.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import backtest
import history
import paper_trading
from config import DEFAULT_RISK_THRESHOLDS
from risk_scan import pool_url

DASHBOARD_DIR = Path(__file__).parent / "docs"
DASHBOARD_DATA_PATH = DASHBOARD_DIR / "data.json"

RECENT_SNAPSHOTS_LIMIT = 60
RECENT_ALERTS_LIMIT = 30
CLOSED_TRADES_LIMIT = 50
CREATOR_WALLETS_LIMIT = 20


def _recent_snapshots(conn: sqlite3.Connection, limit: int = RECENT_SNAPSHOTS_LIMIT) -> list[dict]:
    """Letzte N Snapshots über ALLE Adressen hinweg (nicht pro Adresse) -
    Grundlage für die "Zuletzt gescannt"-Ansicht im Dashboard."""
    rows = conn.execute(
        """SELECT address, symbol, scanned_at, liquidity_usd, score_total, risk_level, pool_address
           FROM snapshots ORDER BY scanned_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [
        {
            "address": address,
            "symbol": symbol,
            "scanned_at": scanned_at,
            "liquidity_usd": liquidity_usd,
            "score": score_total,
            "risk_level": risk_level,
            "chart_url": pool_url(pool_address),
        }
        for address, symbol, scanned_at, liquidity_usd, score_total, risk_level, pool_address in rows
    ]


def _recent_alerts(conn: sqlite3.Connection, limit: int = RECENT_ALERTS_LIMIT) -> list[dict]:
    """Verbindet alerts/rising_alerts (nur Adresse + Zeitpunkt) mit dem
    jeweils letzten Snapshot derselben Adresse für Symbol/Score/Link."""
    rows = conn.execute(
        """
        SELECT address, alerted_at, 'alert' AS kind FROM alerts
        UNION ALL
        SELECT address, alerted_at, 'rising' AS kind FROM rising_alerts
        ORDER BY alerted_at DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()

    alerts = []
    for address, alerted_at, kind in rows:
        snapshot = history.previous_snapshot(conn, address)
        alerts.append({
            "address": address,
            "symbol": snapshot.symbol if snapshot else address,
            "alerted_at": alerted_at,
            "kind": kind,
            "score": snapshot.score_total if snapshot else None,
            "risk_level": snapshot.risk_level if snapshot else None,
            "chart_url": pool_url(snapshot.pool_address) if snapshot else None,
        })
    return alerts


def _stats(conn: sqlite3.Connection) -> dict:
    total_snapshots = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    total_unique_coins = conn.execute("SELECT COUNT(DISTINCT address) FROM snapshots").fetchone()[0]
    total_alerts = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    total_rising_alerts = conn.execute("SELECT COUNT(*) FROM rising_alerts").fetchone()[0]
    risk_level_counts = dict(conn.execute(
        """SELECT risk_level, COUNT(*) FROM (
               SELECT address, risk_level, MAX(scanned_at) FROM snapshots GROUP BY address
           ) GROUP BY risk_level"""
    ).fetchall())
    return {
        "total_snapshots": total_snapshots,
        "total_unique_coins": total_unique_coins,
        "total_alerts": total_alerts,
        "total_rising_alerts": total_rising_alerts,
        "risk_level_counts": risk_level_counts,
    }


def _trade_dict(trade: paper_trading.PaperTrade) -> dict:
    return {
        "address": trade.address,
        "symbol": trade.symbol,
        "entry_at": trade.entry_at,
        "entry_price": trade.entry_price,
        "entry_score": trade.entry_score,
        "entry_risk_level": trade.entry_risk_level,
        "status": trade.status,
        "exit_at": trade.exit_at,
        "exit_price": trade.exit_price,
        "exit_reason": trade.exit_reason,
        "pnl_percent": trade.pnl_percent,
        "pnl_usd": trade.pnl_usd,
    }


def _creator_wallets(conn: sqlite3.Connection, limit: int = CREATOR_WALLETS_LIMIT) -> list[dict]:
    """Track-Record je Creator-Wallet mit mind. 2 bekannten Coins ("Serial-
    Launcher") - pro Coin, ob die Liquidität vom ersten zum letzten
    bekannten Snapshot eingebrochen ist (dieselbe Definition wie
    history.creator_rugged_coin_count). Wallets mit den meisten Rug-Treffern
    zuerst, damit die auffälligsten sofort sichtbar sind.

    Wallets oberhalb von DEFAULT_RISK_THRESHOLDS.serial_launcher_implausible_
    count werden ausgeschlossen - live verifiziert, dass manche updateAuthority-
    Werte gar keine echten Wallets sind, sondern ein von einem Launch-Tool
    geteilter Platzhalter (ein Fall: 142 Coins auf eine on-chain nicht
    existierende Adresse, siehe risk.evaluate_creator_history), kein
    aussagekräftiges Serial-Launcher-Signal."""
    rows = conn.execute(
        """SELECT creator_authority, address, symbol, scanned_at, liquidity_usd, pool_address
           FROM snapshots WHERE creator_authority IS NOT NULL
           ORDER BY creator_authority, address, scanned_at ASC"""
    ).fetchall()

    by_creator: dict[str, dict[str, list]] = {}
    for creator, address, symbol, scanned_at, liquidity_usd, pool_address in rows:
        by_creator.setdefault(creator, {}).setdefault(address, []).append(
            (symbol, liquidity_usd, pool_address)
        )

    wallets = []
    for creator, coins in by_creator.items():
        if len(coins) < 2 or len(coins) > DEFAULT_RISK_THRESHOLDS.serial_launcher_implausible_count:
            continue

        coin_list = []
        rugged_count = 0
        for address, entries in coins.items():
            symbol, first_liquidity, _ = entries[0]
            _, last_liquidity, pool_address = entries[-1]

            rugged = False
            if first_liquidity is not None and first_liquidity > 0 and last_liquidity is not None:
                drop_percent = (first_liquidity - last_liquidity) / first_liquidity * 100
                rugged = drop_percent >= DEFAULT_RISK_THRESHOLDS.liquidity_crash_percent_high
            if rugged:
                rugged_count += 1

            coin_list.append({
                "address": address,
                "symbol": symbol,
                "rugged": rugged,
                "chart_url": pool_url(pool_address),
            })

        wallets.append({
            "creator_authority": creator,
            "coin_count": len(coin_list),
            "rugged_count": rugged_count,
            "coins": coin_list,
        })

    wallets.sort(key=lambda w: (-w["rugged_count"], -w["coin_count"]))
    return wallets[:limit]


def _backtest_section(conn: sqlite3.Connection) -> dict:
    results = backtest.stored_results(conn)
    return {
        "total_backtested": len(results),
        "by_risk_level": backtest.summarize_by_risk_level(results),
    }


def build_dashboard_data(conn: sqlite3.Connection) -> dict:
    paper_trading.ensure_schema(conn)
    backtest.ensure_schema(conn)
    open_trades = paper_trading.open_trades(conn)
    closed_trades = paper_trading.closed_trades(conn)
    summary = paper_trading.summarize(closed_trades)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stats": _stats(conn),
        "recent_snapshots": _recent_snapshots(conn),
        "recent_alerts": _recent_alerts(conn),
        "paper_trading": {
            "open_positions": [_trade_dict(t) for t in open_trades],
            "closed_trades": [_trade_dict(t) for t in closed_trades[-CLOSED_TRADES_LIMIT:]],
            "summary": summary,
        },
        "backtest": _backtest_section(conn),
        "creator_wallets": _creator_wallets(conn),
    }


def main() -> None:
    conn = history.connect()
    try:
        data = build_dashboard_data(conn)
    finally:
        conn.close()

    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    DASHBOARD_DATA_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Dashboard-Daten geschrieben: {DASHBOARD_DATA_PATH}")


if __name__ == "__main__":
    main()
