"""Tests für die reinen Ranking-Hilfsfunktionen aus risk_scan.py - laufen
offline, ohne API-Zugriff (BirdeyeClient wird hier nicht instanziiert)."""
from risk import RiskFinding, Severity, build_report
from risk_scan import (
    TokenAssessment,
    _display_width,
    _pad,
    _top_finding_text,
    format_telegram_summary,
    rank_by_score,
)
from score import Score


def _assessment(symbol, total_score, findings=None, capped=False, address="addr"):
    findings = findings or []
    report = build_report(address, symbol, findings, [])
    score = Score(total=total_score, capped=capped, breakdown=[])
    listing = {"address": address, "symbol": symbol, "liquidityAddedAt": "2026-09-09T00:00:00"}
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


def test_display_width_ascii_matches_character_count():
    assert _display_width("STONK") == 5


def test_display_width_cjk_characters_count_double():
    assert _display_width("牛来") == 4  # 2 Zeichen, je 2 Spalten breit


def test_pad_aligns_wide_and_narrow_strings_to_same_width():
    padded_ascii = _pad("AB", 10)
    padded_cjk = _pad("牛来", 10)
    assert _display_width(padded_ascii) == 10
    assert _display_width(padded_cjk) == 10


def test_telegram_summary_includes_address_for_every_coin():
    results = [
        _assessment("AAA", 80, address="AddrOne111111111111111111111111111111111"),
        _assessment("BBB", 50, address="AddrTwo222222222222222222222222222222222"),
    ]
    text = format_telegram_summary(results)
    assert "AddrOne111111111111111111111111111111111" in text
    assert "AddrTwo222222222222222222222222222222222" in text


def test_telegram_summary_escapes_html_special_characters_in_symbol():
    results = [_assessment("S&P <500>", 80)]
    text = format_telegram_summary(results)
    assert "S&amp;P &lt;500&gt;" in text
    assert "S&P <500>" not in text


def test_telegram_summary_respects_top_n():
    results = [_assessment(f"C{i}", i) for i in range(20)]
    text = format_telegram_summary(results, top_n=3)
    assert "Top 3 von 20" in text


def test_telegram_summary_wraps_address_in_code_tag():
    results = [_assessment("AAA", 80, address="SomeAddress123")]
    text = format_telegram_summary(results)
    assert "<code>SomeAddress123</code>" in text
