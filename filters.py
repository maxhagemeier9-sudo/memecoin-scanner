"""Reine Filterlogik für Token-Daten - unabhängig von der API testbar."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from config import DEFAULT_FILTER_CRITERIA, FilterCriteria


@dataclass(frozen=True)
class FilterResult:
    symbol: str
    name: str
    market_cap: float
    volume_24h: float
    passed: bool
    reason: str = ""


def evaluate_token(
    token: dict[str, Any],
    criteria: FilterCriteria = DEFAULT_FILTER_CRITERIA,
) -> FilterResult:
    symbol = token.get("symbol") or "???"
    name = token.get("name") or "Unknown"
    market_cap = token.get("mc") or 0
    volume_24h = token.get("v24hUSD") or 0

    if market_cap < criteria.min_market_cap:
        reason = f"Market Cap zu niedrig (${market_cap:,.0f})"
        return FilterResult(symbol, name, market_cap, volume_24h, False, reason)

    if market_cap > criteria.max_market_cap:
        reason = f"Market Cap zu hoch (${market_cap:,.0f})"
        return FilterResult(symbol, name, market_cap, volume_24h, False, reason)

    if volume_24h < criteria.min_volume_24h:
        reason = f"Volumen zu niedrig (${volume_24h:,.0f})"
        return FilterResult(symbol, name, market_cap, volume_24h, False, reason)

    return FilterResult(symbol, name, market_cap, volume_24h, True)


def evaluate_tokens(
    tokens: list[dict[str, Any]],
    criteria: FilterCriteria = DEFAULT_FILTER_CRITERIA,
) -> list[FilterResult]:
    return [evaluate_token(token, criteria) for token in tokens]
