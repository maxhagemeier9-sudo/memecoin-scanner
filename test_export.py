"""Tests für den CSV/JSON-Export - laufen offline, schreiben in ein
temporäres Verzeichnis (pytest tmp_path), kein Netzwerkzugriff."""
import csv
import json

from export import export_csv, export_json, export_ranking
from risk import RiskFinding, Severity, build_report
from risk_scan import TokenAssessment
from score import Score


def _entry(symbol, score_total, liquidity_usd=5_000.0, findings=None, capped=False):
    findings = findings or []
    report = build_report(f"addr-{symbol}", symbol, findings, [])
    score = Score(total=score_total, capped=capped, breakdown=[])
    listing = {"address": f"addr-{symbol}", "symbol": symbol, "liquidityAddedAt": "2026-09-09T00:00:00"}
    return listing, TokenAssessment(report=report, score=score, liquidity_usd=liquidity_usd)


def test_export_csv_writes_one_row_per_entry(tmp_path):
    ranked = [_entry("AAA", 80), _entry("BBB", 40)]
    path = tmp_path / "out.csv"

    export_csv(ranked, path)

    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 2
    assert rows[0]["symbol"] == "AAA"
    assert rows[0]["score"] == "80"


def test_export_csv_with_no_entries_writes_header_only(tmp_path):
    path = tmp_path / "empty.csv"
    export_csv([], path)

    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert rows == []


def test_export_json_round_trips_key_fields(tmp_path):
    findings = [RiskFinding(Severity.HOCH, "Top-10-Holder besitzen 99% des Supplys")]
    ranked = [_entry("AAA", 30, findings=findings)]
    path = tmp_path / "out.json"

    export_json(ranked, path)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["symbol"] == "AAA"
    assert data[0]["score"] == 30
    assert data[0]["top_finding"] == "Top-10-Holder besitzen 99% des Supplys"


def test_export_ranking_creates_both_files(tmp_path):
    ranked = [_entry("AAA", 80)]
    csv_path, json_path = export_ranking(ranked, export_dir=tmp_path)

    assert csv_path.exists()
    assert json_path.exists()
    assert csv_path.parent == tmp_path
