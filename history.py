"""Lokale Historie aller gescannten Coins (SQLite, stdlib - keine neue
Dependency). Macht aufeinanderfolgende Scans derselben Adresse vergleichbar
und ist Grundlage für zwei Signale in risk.py:
- evaluate_liquidity_trend: Liquiditäts-Veränderung seit dem letzten Scan.
- evaluate_creator_history: wie viele andere Coins dieselbe Creator-Wallet
  (Token-2022 updateAuthority) schon gelistet hat, UND (creator_rugged_coin_
  count) wie viele davon nachweislich einen Liquiditäts-Einbruch hatten -
  ein tatsächliches Outcome-Signal statt nur einer Zählung.

Jeder Scan fügt eine neue Zeile hinzu (kein Überschreiben), damit die
Historie über die Zeit wächst statt nur den letzten Stand zu kennen.

Die `alerts`-Tabelle hält zusätzlich fest, welche Adressen schon einmal
alarmiert wurden - persistent statt nur im Prozessspeicher, damit ein
Coin nicht erneut gemeldet wird, wenn der Scan (wie bei GitHub Actions)
bei jedem Trigger in einem komplett neuen Prozess läuft. `rising_alerts`
ist dieselbe Idee für eine zweite, unabhängige Alert-Kategorie (siehe
watchlist_candidates/risk_scan.recheck_watchlist) - bewusst eine eigene
Tabelle statt eine Spalte in `alerts`, damit bestehende, bereits gecachte
history.sqlite3-Dateien (z.B. in GitHub Actions) ohne Migration weiterlaufen.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from config import HISTORY_DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    address TEXT NOT NULL,
    symbol TEXT NOT NULL,
    scanned_at TEXT NOT NULL,
    liquidity_usd REAL,
    volume_24h_usd REAL,
    holder_count INTEGER,
    top10_percent REAL,
    score_total INTEGER NOT NULL,
    risk_level TEXT NOT NULL,
    creator_authority TEXT
);
CREATE INDEX IF NOT EXISTS idx_snapshots_address ON snapshots(address);
CREATE INDEX IF NOT EXISTS idx_snapshots_creator ON snapshots(creator_authority);

CREATE TABLE IF NOT EXISTS alerts (
    address TEXT PRIMARY KEY,
    alerted_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rising_alerts (
    address TEXT PRIMARY KEY,
    alerted_at TEXT NOT NULL
);
"""

# Spalten, die nach dem initialen Schema per ALTER TABLE nachgezogen wurden -
# neue Installationen bekommen sie schon über _SCHEMA, bestehende
# history.sqlite3-Dateien (z.B. der GitHub-Actions-Cache) brauchen die
# Migration hier, sonst schlägt der nächste INSERT mit "no column named X"
# fehl. pool_address kam mit dem Wechsel auf GeckoTerminal dazu (Pool- statt
# Token-Adresse nötig, um einen Coin später erneut abzufragen).
_MIGRATIONS = {
    "snapshots": [("price", "REAL"), ("pool_address", "TEXT")],
}

_SNAPSHOT_COLUMNS = (
    "address, symbol, scanned_at, liquidity_usd, volume_24h_usd, holder_count, "
    "top10_percent, score_total, risk_level, creator_authority, price, pool_address"
)


@dataclass(frozen=True)
class Snapshot:
    address: str
    symbol: str
    scanned_at: str
    liquidity_usd: float | None
    volume_24h_usd: float | None
    holder_count: int | None
    top10_percent: float | None
    score_total: int
    risk_level: str
    creator_authority: str | None = None
    price: float | None = None
    pool_address: str | None = None


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in _MIGRATIONS.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, sql_type in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
    conn.commit()


def connect(db_path=HISTORY_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn


def record_snapshot(conn: sqlite3.Connection, snapshot: Snapshot) -> None:
    conn.execute(
        f"""INSERT INTO snapshots ({_SNAPSHOT_COLUMNS})
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            snapshot.address,
            snapshot.symbol,
            snapshot.scanned_at,
            snapshot.liquidity_usd,
            snapshot.volume_24h_usd,
            snapshot.holder_count,
            snapshot.top10_percent,
            snapshot.score_total,
            snapshot.risk_level,
            snapshot.creator_authority,
            snapshot.price,
            snapshot.pool_address,
        ),
    )
    conn.commit()


def previous_snapshot(conn: sqlite3.Connection, address: str) -> Snapshot | None:
    """Der zuletzt gespeicherte Snapshot für diese Adresse - MUSS vor dem
    record_snapshot()-Aufruf des aktuellen Laufs abgefragt werden, sonst
    liefert sie den gerade selbst geschriebenen Snapshot zurück."""
    row = conn.execute(
        f"SELECT {_SNAPSHOT_COLUMNS} FROM snapshots WHERE address = ? ORDER BY scanned_at DESC LIMIT 1",
        (address,),
    ).fetchone()
    return Snapshot(*row) if row else None


def has_been_alerted(conn: sqlite3.Connection, address: str) -> bool:
    row = conn.execute("SELECT 1 FROM alerts WHERE address = ?", (address,)).fetchone()
    return row is not None


def mark_alerted(conn: sqlite3.Connection, address: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO alerts (address, alerted_at) VALUES (?, ?)",
        (address, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def creator_history(conn: sqlite3.Connection, creator_authority: str) -> list[Snapshot]:
    """Alle bisherigen Snapshots von Coins mit dieser Creator-Wallet."""
    rows = conn.execute(
        f"SELECT {_SNAPSHOT_COLUMNS} FROM snapshots WHERE creator_authority = ? ORDER BY scanned_at ASC",
        (creator_authority,),
    ).fetchall()
    return [Snapshot(*row) for row in rows]


def creator_rugged_coin_count(
    conn: sqlite3.Connection,
    creator_authority: str,
    exclude_address: str,
    crash_threshold_percent: float,
) -> int:
    """Zählt, bei wie vielen ANDEREN Coins derselben Creator-Wallet die
    Liquidität vom ersten zum bisher letzten bekannten Snapshot um mindestens
    crash_threshold_percent eingebrochen ist - im Gegensatz zu
    creator_history() (reine Zählung "wie viele Coins hat die Wallet
    gelistet") ein Signal für das TATSÄCHLICHE Ergebnis dieser früheren
    Coins. Coins mit nur einem Snapshot (keine Wiederholung, also kein
    Trend feststellbar) zählen bewusst NICHT als gerugged, um keine
    Fehlalarme aus zu dünnen Daten zu erzeugen."""
    by_address: dict[str, list[Snapshot]] = {}
    for snapshot in creator_history(conn, creator_authority):
        if snapshot.address == exclude_address:
            continue
        by_address.setdefault(snapshot.address, []).append(snapshot)

    rugged = 0
    for snapshots in by_address.values():
        first, last = snapshots[0], snapshots[-1]
        if first.liquidity_usd is None or first.liquidity_usd <= 0 or last.liquidity_usd is None:
            continue
        if first is last:
            continue
        drop_percent = (first.liquidity_usd - last.liquidity_usd) / first.liquidity_usd * 100
        if drop_percent >= crash_threshold_percent:
            rugged += 1

    return rugged


def first_snapshot(conn: sqlite3.Connection, address: str) -> Snapshot | None:
    """Der ALLERERSTE gespeicherte Snapshot für diese Adresse - Referenzpunkt,
    um Wachstum seit der Erstsichtung zu messen (im Gegensatz zu
    previous_snapshot, das den letzten Stand vor dem aktuellen Lauf liefert)."""
    row = conn.execute(
        f"SELECT {_SNAPSHOT_COLUMNS} FROM snapshots WHERE address = ? ORDER BY scanned_at ASC LIMIT 1",
        (address,),
    ).fetchone()
    return Snapshot(*row) if row else None


def has_been_rising_alerted(conn: sqlite3.Connection, address: str) -> bool:
    row = conn.execute("SELECT 1 FROM rising_alerts WHERE address = ?", (address,)).fetchone()
    return row is not None


def mark_rising_alerted(conn: sqlite3.Connection, address: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO rising_alerts (address, alerted_at) VALUES (?, ?)",
        (address, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def watchlist_candidates(conn: sqlite3.Connection, limit: int) -> list[Snapshot]:
    """Bereits mehrfach gescannte Coins, deren zuletzt bekanntes Risiko-Level
    NIEDRIG oder MITTEL ist - Kandidaten für die Wachstums-Rückprüfung
    (siehe risk_scan.recheck_watchlist), älteste zuerst geprüft (Round-Robin)."""
    columns = ", ".join(f"s1.{col.strip()}" for col in _SNAPSHOT_COLUMNS.split(","))
    rows = conn.execute(
        f"""
        SELECT {columns}
        FROM snapshots s1
        WHERE s1.scanned_at = (SELECT MAX(s2.scanned_at) FROM snapshots s2 WHERE s2.address = s1.address)
          AND s1.risk_level IN ('NIEDRIG', 'MITTEL')
          AND s1.address IN (SELECT address FROM snapshots GROUP BY address HAVING COUNT(*) >= 2)
        ORDER BY s1.scanned_at ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [Snapshot(*row) for row in rows]


def all_first_snapshots(conn: sqlite3.Connection) -> list[Snapshot]:
    """Der jeweils erste Snapshot für JEDE bekannte Adresse - Grundlage fürs
    Backtesting (siehe backtest.py): Score/Preis zum Zeitpunkt der
    Erstsichtung, um später gegen die tatsächliche Kursentwicklung
    (OHLCV) zu vergleichen."""
    columns = ", ".join(f"s1.{col.strip()}" for col in _SNAPSHOT_COLUMNS.split(","))
    rows = conn.execute(
        f"""
        SELECT {columns}
        FROM snapshots s1
        WHERE s1.scanned_at = (SELECT MIN(s2.scanned_at) FROM snapshots s2 WHERE s2.address = s1.address)
        ORDER BY s1.scanned_at ASC
        """
    ).fetchall()
    return [Snapshot(*row) for row in rows]
