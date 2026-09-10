"""Tests für die Alters-Berechnung/-Formatierung - laufen offline."""
from datetime import datetime, timedelta, timezone

from new_coins import age_minutes, format_age


def test_age_minutes_computes_difference_to_reference_time():
    now = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)
    listed_at = (now - timedelta(minutes=42)).isoformat()
    assert round(age_minutes(listed_at, now=now)) == 42


def test_format_age_under_one_hour_shows_minutes():
    assert format_age(15) == "15 Min"


def test_format_age_under_one_day_shows_hours():
    assert format_age(150) == "2.5 Std"


def test_format_age_over_one_day_shows_days():
    assert format_age(60 * 24 * 3) == "3.0 Tage"
