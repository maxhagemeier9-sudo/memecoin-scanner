"""Tests für die lokale Historie - laufen offline mit einer In-Memory-SQLite-DB."""
import pytest

from history import (
    Snapshot,
    all_first_snapshots,
    connect,
    creator_history,
    first_snapshot,
    has_been_alerted,
    has_been_rising_alerted,
    mark_alerted,
    mark_rising_alerted,
    previous_snapshot,
    record_snapshot,
    watchlist_candidates,
)


@pytest.fixture
def conn():
    connection = connect(db_path=":memory:")
    yield connection
    connection.close()


def _snapshot(address="addr1", symbol="ABC", scanned_at="2026-09-09T00:00:00+00:00", **overrides):
    params = dict(
        address=address,
        symbol=symbol,
        scanned_at=scanned_at,
        liquidity_usd=10_000.0,
        volume_24h_usd=5_000.0,
        holder_count=100,
        top10_percent=40.0,
        score_total=70,
        risk_level="MITTEL",
        creator_authority=None,
        price=0.001,
    )
    params.update(overrides)
    return Snapshot(**params)


def test_previous_snapshot_is_none_when_never_scanned(conn):
    assert previous_snapshot(conn, "unknown-address") is None


def test_record_and_retrieve_snapshot(conn):
    record_snapshot(conn, _snapshot())
    result = previous_snapshot(conn, "addr1")
    assert result is not None
    assert result.address == "addr1"
    assert result.liquidity_usd == 10_000.0


def test_previous_snapshot_returns_most_recent(conn):
    record_snapshot(conn, _snapshot(scanned_at="2026-09-09T00:00:00+00:00", liquidity_usd=1_000.0))
    record_snapshot(conn, _snapshot(scanned_at="2026-09-09T00:10:00+00:00", liquidity_usd=2_000.0))
    result = previous_snapshot(conn, "addr1")
    assert result.liquidity_usd == 2_000.0


def test_creator_history_returns_only_matching_creator(conn):
    record_snapshot(conn, _snapshot(address="a1", creator_authority="creatorX"))
    record_snapshot(conn, _snapshot(address="a2", creator_authority="creatorX"))
    record_snapshot(conn, _snapshot(address="a3", creator_authority="creatorY"))

    results = creator_history(conn, "creatorX")
    assert {s.address for s in results} == {"a1", "a2"}


def test_creator_history_is_empty_for_unknown_creator(conn):
    assert creator_history(conn, "never-seen") == []


def test_snapshots_for_different_addresses_are_independent(conn):
    record_snapshot(conn, _snapshot(address="a1", liquidity_usd=1_000.0))
    record_snapshot(conn, _snapshot(address="a2", liquidity_usd=2_000.0))
    assert previous_snapshot(conn, "a1").liquidity_usd == 1_000.0
    assert previous_snapshot(conn, "a2").liquidity_usd == 2_000.0


def test_has_been_alerted_is_false_for_unknown_address(conn):
    assert has_been_alerted(conn, "unknown") is False


def test_mark_alerted_then_has_been_alerted_is_true(conn):
    mark_alerted(conn, "addr1")
    assert has_been_alerted(conn, "addr1") is True


def test_mark_alerted_is_idempotent(conn):
    mark_alerted(conn, "addr1")
    mark_alerted(conn, "addr1")  # darf nicht crashen (PRIMARY KEY)
    assert has_been_alerted(conn, "addr1") is True


def test_other_addresses_remain_unaffected_by_mark_alerted(conn):
    mark_alerted(conn, "addr1")
    assert has_been_alerted(conn, "addr2") is False


def test_first_snapshot_is_none_when_never_scanned(conn):
    assert first_snapshot(conn, "unknown") is None


def test_first_snapshot_returns_the_oldest_not_the_newest(conn):
    record_snapshot(conn, _snapshot(scanned_at="2026-09-09T00:00:00+00:00", liquidity_usd=1_000.0))
    record_snapshot(conn, _snapshot(scanned_at="2026-09-09T00:10:00+00:00", liquidity_usd=5_000.0))
    result = first_snapshot(conn, "addr1")
    assert result.liquidity_usd == 1_000.0


def test_rising_alert_tracking_is_independent_of_regular_alerts(conn):
    mark_alerted(conn, "addr1")
    assert has_been_rising_alerted(conn, "addr1") is False
    mark_rising_alerted(conn, "addr1")
    assert has_been_rising_alerted(conn, "addr1") is True
    assert has_been_alerted(conn, "addr1") is True  # unveraendert


def test_watchlist_candidates_excludes_single_scan_addresses(conn):
    record_snapshot(conn, _snapshot(address="a1", risk_level="MITTEL"))  # nur 1x gescannt
    results = watchlist_candidates(conn, limit=10)
    assert results == []


def test_watchlist_candidates_excludes_hoch_risk(conn):
    record_snapshot(conn, _snapshot(address="a1", scanned_at="2026-09-09T00:00:00+00:00", risk_level="HOCH"))
    record_snapshot(conn, _snapshot(address="a1", scanned_at="2026-09-09T00:10:00+00:00", risk_level="HOCH"))
    results = watchlist_candidates(conn, limit=10)
    assert results == []


def test_watchlist_candidates_includes_mittel_and_niedrig_multi_scan_addresses(conn):
    for risk in ("MITTEL", "NIEDRIG"):
        record_snapshot(conn, _snapshot(address=f"a-{risk}", scanned_at="2026-09-09T00:00:00+00:00", risk_level=risk))
        record_snapshot(conn, _snapshot(address=f"a-{risk}", scanned_at="2026-09-09T00:10:00+00:00", risk_level=risk))

    results = watchlist_candidates(conn, limit=10)
    assert {s.address for s in results} == {"a-MITTEL", "a-NIEDRIG"}


def test_watchlist_candidates_respects_limit(conn):
    for i in range(5):
        record_snapshot(conn, _snapshot(address=f"a{i}", scanned_at=f"2026-09-09T00:0{i}:00+00:00", risk_level="MITTEL"))
        record_snapshot(conn, _snapshot(address=f"a{i}", scanned_at=f"2026-09-09T00:1{i}:00+00:00", risk_level="MITTEL"))

    results = watchlist_candidates(conn, limit=2)
    assert len(results) == 2


def test_price_round_trips_through_record_and_retrieve(conn):
    record_snapshot(conn, _snapshot(price=0.00012345))
    result = previous_snapshot(conn, "addr1")
    assert result.price == 0.00012345


def test_price_defaults_to_none_when_not_given():
    snapshot = Snapshot(
        address="addr1", symbol="ABC", scanned_at="2026-09-09T00:00:00+00:00",
        liquidity_usd=1_000.0, volume_24h_usd=500.0, holder_count=10,
        top10_percent=50.0, score_total=40, risk_level="HOCH",
    )
    assert snapshot.price is None
    assert snapshot.pool_address is None


def test_pool_address_round_trips_through_record_and_retrieve(conn):
    record_snapshot(conn, _snapshot(pool_address="PoolAddr123"))
    result = previous_snapshot(conn, "addr1")
    assert result.pool_address == "PoolAddr123"


def test_all_first_snapshots_returns_one_row_per_address(conn):
    record_snapshot(conn, _snapshot(address="a1", scanned_at="2026-09-09T00:00:00+00:00", price=1.0))
    record_snapshot(conn, _snapshot(address="a1", scanned_at="2026-09-09T00:10:00+00:00", price=2.0))
    record_snapshot(conn, _snapshot(address="a2", scanned_at="2026-09-09T00:05:00+00:00", price=5.0))

    results = all_first_snapshots(conn)

    by_address = {s.address: s for s in results}
    assert set(by_address) == {"a1", "a2"}
    assert by_address["a1"].price == 1.0  # der erste, nicht der letzte Snapshot
    assert by_address["a2"].price == 5.0


def test_all_first_snapshots_is_empty_for_fresh_db(conn):
    assert all_first_snapshots(conn) == []


def test_connect_migrates_pre_existing_db_without_price_column(tmp_path):
    """Simuliert eine bereits bestehende history.sqlite3 von vor der
    price-Spalte (z.B. der GitHub-Actions-Cache) - connect() muss sie ohne
    Datenverlust nachziehen, sonst schlägt der nächste record_snapshot()
    mit "no column named price" fehl."""
    import sqlite3

    db_path = tmp_path / "old.sqlite3"
    old_conn = sqlite3.connect(db_path)
    old_conn.execute(
        """CREATE TABLE snapshots (
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
        )"""
    )
    old_conn.execute(
        "INSERT INTO snapshots (address, symbol, scanned_at, score_total, risk_level) "
        "VALUES ('addr1', 'OLD', '2026-09-09T00:00:00+00:00', 50, 'MITTEL')"
    )
    old_conn.commit()
    old_conn.close()

    migrated_conn = connect(db_path=str(db_path))
    try:
        # alte Zeile bleibt erhalten, price ist NULL statt eines Fehlers
        old_row = previous_snapshot(migrated_conn, "addr1")
        assert old_row is not None
        assert old_row.price is None

        # neue Zeilen koennen price setzen
        record_snapshot(migrated_conn, _snapshot(address="addr2", price=0.5))
        assert previous_snapshot(migrated_conn, "addr2").price == 0.5
    finally:
        migrated_conn.close()
