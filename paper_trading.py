"""Paper Trading: simuliert Trades anhand unserer eigenen Scan-Signale
(monitor._should_alert / is_rising) und protokolliert, ob die Strategie
tatsächlich profitabel gewesen wäre - ohne echtes Geld zu riskieren.

Eigene Tabelle (paper_trades) in derselben history.sqlite3-Datei, aber mit
eigener Schema-Verwaltung hier statt in history.py, damit history.py auf
"Scan-Snapshots" fokussiert bleibt - ensure_schema() ist idempotent
(CREATE TABLE IF NOT EXISTS) und wird bei jedem scan_once()-Aufruf in
monitor.py aufgerufen.

Exit-Regeln (siehe config.py): Take-Profit, Stop-Loss, maximale Haltedauer -
wer zuerst greift, schließt die Position.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

import history
from config import (
    PAPER_MAX_HOLD_MINUTES,
    PAPER_STOP_LOSS_PERCENT,
    PAPER_TAKE_PROFIT_PERCENT,
    PAPER_TRADE_SIZE_USD,
)
from geckoterminal_client import GeckoTerminalAPIError, GeckoTerminalClient
from telegram_alerts import TelegramError, send_telegram_message

_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    address TEXT NOT NULL,
    symbol TEXT NOT NULL,
    entry_at TEXT NOT NULL,
    entry_price REAL NOT NULL,
    entry_score INTEGER NOT NULL,
    entry_risk_level TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN',
    exit_at TEXT,
    exit_price REAL,
    exit_reason TEXT,
    pnl_percent REAL,
    pnl_usd REAL
);
CREATE INDEX IF NOT EXISTS idx_paper_trades_address ON paper_trades(address);
CREATE INDEX IF NOT EXISTS idx_paper_trades_status ON paper_trades(status);

CREATE TABLE IF NOT EXISTS paper_trading_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_ROW_COLUMNS = (
    "id, address, symbol, entry_at, entry_price, entry_score, entry_risk_level, "
    "status, exit_at, exit_price, exit_reason, pnl_percent, pnl_usd"
)


@dataclass(frozen=True)
class PaperTrade:
    id: int
    address: str
    symbol: str
    entry_at: str
    entry_price: float
    entry_score: int
    entry_risk_level: str
    status: str
    exit_at: str | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    pnl_percent: float | None = None
    pnl_usd: float | None = None


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


def has_open_trade(conn: sqlite3.Connection, address: str) -> bool:
    row = conn.execute("SELECT 1 FROM paper_trades WHERE address = ? AND status = 'OPEN'", (address,)).fetchone()
    return row is not None


def open_trade(
    conn: sqlite3.Connection,
    address: str,
    symbol: str,
    entry_price: float,
    entry_score: int,
    entry_risk_level: str,
) -> None:
    conn.execute(
        """INSERT INTO paper_trades
           (address, symbol, entry_at, entry_price, entry_score, entry_risk_level, status)
           VALUES (?, ?, ?, ?, ?, ?, 'OPEN')""",
        (address, symbol, datetime.now(timezone.utc).isoformat(), entry_price, entry_score, entry_risk_level),
    )
    conn.commit()


def open_trades(conn: sqlite3.Connection) -> list[PaperTrade]:
    rows = conn.execute(f"SELECT {_ROW_COLUMNS} FROM paper_trades WHERE status = 'OPEN'").fetchall()
    return [PaperTrade(*row) for row in rows]


def closed_trades(conn: sqlite3.Connection) -> list[PaperTrade]:
    rows = conn.execute(f"SELECT {_ROW_COLUMNS} FROM paper_trades WHERE status = 'CLOSED' ORDER BY exit_at ASC").fetchall()
    return [PaperTrade(*row) for row in rows]


def close_trade(
    conn: sqlite3.Connection,
    trade_id: int,
    exit_price: float,
    exit_reason: str,
    trade_size_usd: float = PAPER_TRADE_SIZE_USD,
) -> None:
    row = conn.execute(f"SELECT {_ROW_COLUMNS} FROM paper_trades WHERE id = ?", (trade_id,)).fetchone()
    if row is None:
        return
    entry_price = PaperTrade(*row).entry_price

    pnl_percent = (exit_price - entry_price) / entry_price * 100
    pnl_usd = trade_size_usd * pnl_percent / 100

    conn.execute(
        """UPDATE paper_trades
           SET status = 'CLOSED', exit_at = ?, exit_price = ?, exit_reason = ?, pnl_percent = ?, pnl_usd = ?
           WHERE id = ?""",
        (datetime.now(timezone.utc).isoformat(), exit_price, exit_reason, pnl_percent, pnl_usd, trade_id),
    )
    conn.commit()


def decide_exit(trade: PaperTrade, current_price: float, now: datetime | None = None) -> str | None:
    """Reine Exit-Regel-Logik, offline testbar: gibt den Exit-Grund zurück
    (TAKE_PROFIT/STOP_LOSS/MAX_HOLD), oder None, wenn die Position offen bleibt."""
    pnl_percent = (current_price - trade.entry_price) / trade.entry_price * 100

    if pnl_percent >= PAPER_TAKE_PROFIT_PERCENT:
        return "TAKE_PROFIT"
    if pnl_percent <= PAPER_STOP_LOSS_PERCENT:
        return "STOP_LOSS"

    reference_now = now or datetime.now(timezone.utc)
    entry_time = datetime.fromisoformat(trade.entry_at)
    held_minutes = (reference_now - entry_time).total_seconds() / 60
    if held_minutes >= PAPER_MAX_HOLD_MINUTES:
        return "MAX_HOLD"

    return None


def process_open_trades(
    client: GeckoTerminalClient,
    conn: sqlite3.Connection,
) -> list[tuple[PaperTrade, str, float]]:
    """Prüft alle offenen Positionen gegen den aktuellen Preis (ein leichter
    Token-Preis-Call pro Position, kein voller assess_listing) und schließt
    sie, falls eine Exit-Regel greift. Gibt (Trade, Exit-Grund, Exit-Preis)
    für jede geschlossene Position zurück."""
    closed = []

    for trade in open_trades(conn):
        try:
            current_price = client.get_token_price(trade.address)
        except GeckoTerminalAPIError:
            continue

        if current_price is None:
            continue

        reason = decide_exit(trade, current_price)
        if reason is not None:
            close_trade(conn, trade.id, current_price, reason)
            closed.append((trade, reason, current_price))

    return closed


def summarize(trades: list[PaperTrade]) -> dict:
    if not trades:
        return {"count": 0, "win_rate_percent": 0.0, "total_pnl_usd": 0.0, "avg_pnl_percent": 0.0}

    wins = [t for t in trades if (t.pnl_percent or 0) > 0]
    return {
        "count": len(trades),
        "win_rate_percent": len(wins) / len(trades) * 100,
        "total_pnl_usd": sum(t.pnl_usd or 0 for t in trades),
        "avg_pnl_percent": sum(t.pnl_percent or 0 for t in trades) / len(trades),
    }


def _get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM paper_trading_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO paper_trading_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def format_daily_summary(conn: sqlite3.Connection) -> str:
    open_ = open_trades(conn)
    closed = closed_trades(conn)
    summary = summarize(closed)

    lines = [
        "📊 Paper-Trading-Tagesreport",
        f"Offene Positionen: {len(open_)}",
        f"Geschlossene Positionen: {summary['count']}",
    ]
    if summary["count"] > 0:
        lines.append(f"Win-Rate: {summary['win_rate_percent']:.0f}%")
        lines.append(f"Ø P&L: {summary['avg_pnl_percent']:+.1f}%")
        lines.append(f"Gesamt-P&L (bei je ${PAPER_TRADE_SIZE_USD:.0f} pro Trade): ${summary['total_pnl_usd']:+.2f}")
    return "\n".join(lines)


_DAILY_SUMMARY_META_KEY = "last_daily_summary_date"


def maybe_send_daily_summary(conn: sqlite3.Connection, now: datetime | None = None) -> bool:
    """Schickt höchstens einmal pro UTC-Kalendertag eine Performance-Übersicht
    an Telegram, damit es bei einem 10-Minuten-Scan-Intervall nicht spammt.
    Der Zeitpunkt innerhalb des Tages ist bewusst egal (einfach beim ersten
    Scan nach Tageswechsel) - Datum wird erst nach erfolgreichem Versand
    vermerkt, damit ein fehlgeschlagener Versand beim nächsten Scan
    automatisch erneut versucht wird. Gibt zurück, ob tatsächlich gesendet
    wurde (nützlich für Tests und CI-Logs)."""
    today = (now or datetime.now(timezone.utc)).date().isoformat()
    if _get_meta(conn, _DAILY_SUMMARY_META_KEY) == today:
        return False

    try:
        send_telegram_message(format_daily_summary(conn))
    except TelegramError as exc:
        print(f"   (Paper-Trading-Tagesreport fehlgeschlagen: {exc})")
        return False

    _set_meta(conn, _DAILY_SUMMARY_META_KEY, today)
    return True


def _print_report(conn: sqlite3.Connection) -> None:
    closed = closed_trades(conn)
    open_ = open_trades(conn)
    summary = summarize(closed)

    print(f"Offene Positionen: {len(open_)}")
    print(f"Geschlossene Positionen: {summary['count']}")
    if summary["count"] > 0:
        print(f"Win-Rate: {summary['win_rate_percent']:.0f}%")
        print(f"Ø P&L: {summary['avg_pnl_percent']:+.1f}%")
        print(f"Gesamt-P&L (bei je ${PAPER_TRADE_SIZE_USD:.0f} pro Trade): ${summary['total_pnl_usd']:+.2f}")

    if closed:
        print("\nLetzte geschlossene Trades:")
        for t in closed[-10:]:
            print(f"  {t.symbol:10} | {t.exit_reason:12} | {t.pnl_percent:+7.1f}% | {t.address}")


def main() -> None:
    conn = history.connect()
    try:
        ensure_schema(conn)
        _print_report(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
