"""Tests für die reinen Ranking-Hilfsfunktionen aus risk_scan.py - laufen
offline, ohne API-Zugriff (BirdeyeClient wird hier nicht instanziiert)."""
from risk import RiskFinding, Severity, build_report
from risk_scan import TokenAssessment, _top_finding_text, rank_by_score
from score import Score


def _assessment(symbol, total_score, findings=None, capped=False):
    findings = findings or []
    report = build_report("addr", symbol, findings, [])
    score = Score(total=total_score, capped=capped, breakdown=[])
    listing = {"address": "addr", "symbol": symbol, "liquidityAddedAt": "2026-09-09T00:00:00"}
    return listing, TokenAssessment(report=report, score=score, liquidity_usd=1_000)


def test_rank_by_score_sorts_descending():
    results = [_assessment("LOW", 10), _assessment("HIGH", 90), _assessment("MID", 50)]
    ranked = rank_by_score(results, top_n=10)
    assert [a.report.symbol for _, a in ranked] == ["HIGH", "MID", "LOW"]


def test_rank_by_score_truncates_to_top_n():
    results = [_assessment(f"C{i}", i) for i in range(20)]
    ranked = rank_by_score(results, top_n=5)
    assert len(ranked) == 5
    assert [a.score.total for _, a in ranked] == [19, 18, 17, 16, 15]


def test_rank_by_score_top_n_larger_than_results_returns_all():
    results = [_assessment("A", 10), _assessment("B", 20)]
    ranked = rank_by_score(results, top_n=20)
    assert len(ranked) == 2


def test_top_finding_text_picks_highest_severity():
    findings = [
        RiskFinding(Severity.MITTEL, "kleinere Sache"),
        RiskFinding(Severity.HOCH, "größeres Problem"),
    ]
    report = build_report("addr", "SYM", findings, [])
    assert _top_finding_text(report) == "größeres Problem"


def test_top_finding_text_with_no_findings():
    report = build_report("addr", "SYM", [], [])
    assert _top_finding_text(report) == "keine Auffälligkeiten"


def test_top_finding_text_truncates_long_messages():
    long_message = "x" * 100
    report = build_report("addr", "SYM", [RiskFinding(Severity.HOCH, long_message)], [])
    result = _top_finding_text(report, max_len=20)
    assert len(result) == 20
    assert result.endswith("…")
