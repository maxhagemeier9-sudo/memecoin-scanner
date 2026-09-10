"""Export der Ranking-Ergebnisse als CSV/JSON, damit man außerhalb vom
Terminal weiterarbeiten kann (Spreadsheet, eigenes Tracking, etc.).

Nimmt bewusst nur duck-typed (listing, assessment)-Paare entgegen (statt den
TokenAssessment-Typ aus risk_scan.py zu importieren), damit es keine
Rückwärts-Abhängigkeit auf risk_scan.py gibt.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import EXPORT_DIR

_FIELDNAMES = [
    "symbol", "address", "score", "capped", "risk_level",
    "liquidity_usd", "top_finding", "listed_at",
]


def _row_dict(listing: dict, assessment: Any) -> dict:
    report, score = assessment.report, assessment.score
    return {
        "symbol": report.symbol,
        "address": report.address,
        "score": score.total,
        "capped": score.capped,
        "risk_level": report.overall.name,
        "liquidity_usd": assessment.liquidity_usd,
        "top_finding": report.findings[0].message if report.findings else "",
        "listed_at": listing.get("liquidityAddedAt"),
    }


def export_csv(ranked: list[tuple[dict, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [_row_dict(listing, assessment) for listing, assessment in ranked]

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def export_json(ranked: list[tuple[dict, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [_row_dict(listing, assessment) for listing, assessment in ranked]
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")


def export_ranking(ranked: list[tuple[dict, Any]], export_dir: Path = EXPORT_DIR) -> tuple[Path, Path]:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    csv_path = export_dir / f"ranking_{timestamp}.csv"
    json_path = export_dir / f"ranking_{timestamp}.json"

    export_csv(ranked, csv_path)
    export_json(ranked, json_path)

    print(f"\nExportiert: {csv_path.name}, {json_path.name} (in {export_dir}/)")
    return csv_path, json_path
