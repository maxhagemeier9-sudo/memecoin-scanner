"""Dünner Client für die Birdeye-API.

Bündelt Session, Header und Fehlerbehandlung an einer Stelle, damit
Scan-Logik und einzelne Checks nicht jeweils ihren eigenen requests-Aufruf
mit eigenem Error-Handling mitbringen müssen.
"""
from __future__ import annotations

import time
from typing import Any

import requests

from config import (
    BIRDEYE_API_KEY,
    BIRDEYE_BASE_URL,
    DEFAULT_CHAIN,
    MAX_RETRIES,
    REQUEST_TIMEOUT_SECONDS,
    RETRY_BACKOFF_SECONDS,
)


class BirdeyeAPIError(RuntimeError):
    """Wird ausgelöst, wenn die Birdeye-API einen Fehler zurückgibt."""


class BirdeyeClient:
    def __init__(self, api_key: str = BIRDEYE_API_KEY, chain: str = DEFAULT_CHAIN) -> None:
        self._session = requests.Session()
        self._session.headers.update({"X-API-KEY": api_key, "x-chain": chain})

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{BIRDEYE_BASE_URL}{path}"

        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self._session.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            except requests.RequestException as exc:
                last_error = exc
            else:
                if response.status_code == 200:
                    return response.json()

                # 429 (Rate-Limit) und 5xx sind vorübergehend - retry lohnt sich.
                if response.status_code == 429 or response.status_code >= 500:
                    last_error = BirdeyeAPIError(
                        f"{path} -> HTTP {response.status_code}: {response.text[:200]}"
                    )
                else:
                    raise BirdeyeAPIError(
                        f"{path} -> HTTP {response.status_code}: {response.text[:200]}"
                    )

            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)

        raise BirdeyeAPIError(f"{path} nach {MAX_RETRIES} Versuchen fehlgeschlagen: {last_error}")

    def get_token_list(
        self,
        sort_by: str = "v24hUSD",
        sort_type: str = "desc",
        offset: int = 0,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        data = self._get(
            "/defi/tokenlist",
            {"sort_by": sort_by, "sort_type": sort_type, "offset": offset, "limit": limit},
        )
        return data.get("data", {}).get("tokens", [])

    def get_trade_data(self, address: str, period: str = "24h") -> dict[str, Any]:
        data = self._get(
            "/defi/v3/token/trade-data/single",
            {"address": address, "type": period},
        )
        return data.get("data", {})

    def get_price(self, address: str) -> dict[str, Any]:
        data = self._get("/defi/price", {"address": address})
        return data.get("data", {})

    def get_new_listings(self, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        data = self._get("/defi/v2/tokens/new_listing", {"limit": limit, "offset": offset})
        return data.get("data", {}).get("items", [])

    def get_market_data(self, address: str) -> dict[str, Any]:
        data = self._get("/defi/v3/token/market-data", {"address": address})
        return data.get("data", {})

    def get_holders(self, address: str, limit: int = 1) -> dict[str, Any]:
        """Liefert u.a. `holder` (Gesamtzahl) und `top10_hold_percent`."""
        data = self._get(
            "/defi/v3/token/holder",
            {"address": address, "offset": 0, "limit": limit},
        )
        return data.get("data", {})

    def get_ohlcv(
        self,
        address: str,
        type_: str = "1H",
        time_from: int | None = None,
        time_to: int | None = None,
    ) -> list[dict[str, Any]]:
        """Historische Kerzen (o/h/l/c/v je Zeitfenster) - Grundlage fürs
        Backtesting (siehe backtest.py). time_from/time_to sind Unix-Timestamps."""
        params: dict[str, Any] = {"address": address, "type": type_}
        if time_from is not None:
            params["time_from"] = time_from
        if time_to is not None:
            params["time_to"] = time_to
        data = self._get("/defi/v3/ohlcv", params)
        return data.get("data", {}).get("items", [])

    def get_top_traders(
        self,
        address: str,
        sort_by: str = "volume",
        sort_type: str = "desc",
        offset: int = 0,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Top-Trader eines Tokens (für spätere Smart-Money-Erkennung)."""
        data = self._get(
            "/defi/v2/tokens/top_traders",
            {"address": address, "sort_by": sort_by, "sort_type": sort_type, "offset": offset, "limit": limit},
        )
        return data.get("data", {}).get("items", [])
