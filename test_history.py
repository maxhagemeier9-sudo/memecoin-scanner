"""Tests für die lokale Historie - laufen offline mit einer In-Memory-SQLite-DB."""
import pytest

from history import (
    Snapshot,
    connect,
    creator_history,
    has_been_alerted,
    mark_alerted,
    previous_snapshot,
    record_snapshot,
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
