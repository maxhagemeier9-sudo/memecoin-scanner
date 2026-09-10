"""Lokale Historie aller gescannten Coins (SQLite, stdlib - keine neue
Dependency). Macht aufeinanderfolgende Scans derselben Adresse vergleichbar
und ist Grundlage für zwei Signale in risk.py:
- evaluate_liquidity_trend: Liquiditäts-Veränderung seit dem letzten Scan.
- evaluate_creator_history: wie viele andere Coins dieselbe Creator-Wallet
  (Token-2022 updateAuthority) schon gelistet hat.

Jeder Scan fügt eine neue Zeile hinzu (kein Überschreiben), damit die
Historie über die Zeit wächst statt nur den letzten Stand zu kennen.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

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
"""


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


def connect(db_path=HISTORY_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    return conn


def record_snapshot(conn: sqlite3.Connection, snapshot: Snapshot) -> None:
    conn.execute(
        """INSERT INTO snapshots
           (address, symbol, scanned_at, liquidity_usd, volume_24h_usd, holder_count,
            top10_percent, score_total, risk_level, creator_authority)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
        ),
    )
    conn.commit()


def previous_snapshot(conn: sqlite3.Connection, address: str) -> Snapshot | None:
    """Der zuletzt gespeicherte Snapshot für diese Adresse - MUSS vor dem
    record_snapshot()-Aufruf des aktuellen Laufs abgefragt werden, sonst
    liefert sie den gerade selbst geschriebenen Snapshot zurück."""
    row = conn.execute(
        """SELECT address, symbol, scanned_at, liquidity_usd, volume_24h_usd, holder_count,
                  top10_percent, score_total, risk_level, creator_authority
           FROM snapshots WHERE address = ? ORDER BY scanned_at DESC LIMIT 1""",
        (address,),
    ).fetchone()
    return Snapshot(*row) if row else None


def creator_history(conn: sqlite3.Connection, creator_authority: str) -> list[Snapshot]:
    """Alle bisherigen Snapshots von Coins mit dieser Creator-Wallet."""
    rows = conn.execute(
        """SELECT address, symbol, scanned_at, liquidity_usd, volume_24h_usd, holder_count,
                  top10_percent, score_total, risk_level, creator_authority
           FROM snapshots WHERE creator_authority = ? ORDER BY scanned_at ASC""",
        (creator_authority,),
    ).fetchall()
    return [Snapshot(*row) for row in rows]
