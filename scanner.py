"""Orchestriert Abruf und Filterung der Token-Liste."""
from __future__ import annotations

from birdeye_client import BirdeyeClient
from config import DEFAULT_FILTER_CRITERIA, FilterCriteria
from filters import FilterResult, evaluate_tokens


def scan(
    client: BirdeyeClient,
    criteria: FilterCriteria = DEFAULT_FILTER_CRITERIA,
    limit: int = 50,
) -> list[FilterResult]:
    tokens = client.get_token_list(limit=limit)
    return evaluate_tokens(tokens, criteria)
