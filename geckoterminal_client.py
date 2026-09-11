"""Dünner Client für die GeckoTerminal/CoinGecko On-Chain API.

Komplett kostenlos, kein API-Key nötig (Public/Keyless-Tier, siehe
config.GECKOTERMINAL_BASE_URL) - ersetzt Birdeye seit dessen Compute-Units-
Kontingent am 2026-09-10 erschöpft wurde (Reset erst 2026-10-08). Live
verifiziert: new_pools (Discovery), Preis/Liquidität/Volumen/Trade-Zahlen in
einem einzigen Call, OHLCV. NICHT verfügbar im Gratis-Tier: Holder-Daten
(401/429 in Tests) - dafür springt solana_rpc.get_top10_concentration
(Helius) ein.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests

from config import (
    GECKOTERMINAL_BASE_URL,
    GECKOTERMINAL_THROTTLE_SECONDS,
    MAX_RETRIES,
    REQUEST_TIMEOUT_SECONDS,
    RETRY_BACKOFF_SECONDS,
)


class GeckoTerminalAPIError(RuntimeError):
    """Wird ausgelöst, wenn die GeckoTerminal-API einen Fehler zurückgibt."""


@dataclass(frozen=True)
class PoolListing:
    pool_address: str
    token_address: str
    symbol: str
    created_at: str
    price_usd: float | None
    liquidity_usd: float | None
    volume_24h_usd: float | None
    trade_24h: int
    unique_wallet_24h: int
    dex: str | None = None
    # Nur in der Pool-DETAIL-Antwort (get_pool) vorhanden, NICHT in der
    # new_pools-Liste - für ganz frische Coins auf einer Bonding Curve
    # (z.B. pump.fun vor der Migration) strukturell None, da dort noch kein
    # klassischer LP-Token existiert, den man sperren könnte.
    locked_liquidity_percentage: float | None = None


def _strip_network_prefix(token_id: str, network: str) -> str:
    prefix = f"{network}_"
    return token_id[len(prefix):] if token_id.startswith(prefix) else token_id


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_pool(pool: dict[str, Any], network: str = "solana") -> PoolListing | None:
    """Wandelt einen rohen new_pools/pools-Eintrag in ein PoolListing um.
    None, wenn die Basis-Token-Adresse fehlt (sollte praktisch nie passieren,
    aber besser robust als mit einem KeyError abbrechen).

    unique_wallet_24h ist eine Näherung (buyers + sellers je Zeitfenster) -
    GeckoTerminal liefert keine echte Unique-Wallet-Zahl, ein Wallet, das
    sowohl kauft als auch verkauft, wird also doppelt gezählt.
    """
    attrs = pool.get("attributes", {})
    relationships = pool.get("relationships", {})
    token_id = (relationships.get("base_token", {}).get("data") or {}).get("id")
    if not token_id:
        return None

    name = attrs.get("name") or ""
    symbol = name.split(" / ")[0].strip() if " / " in name else name.strip()

    h24_tx = (attrs.get("transactions") or {}).get("h24") or {}
    trade_24h = (h24_tx.get("buys") or 0) + (h24_tx.get("sells") or 0)
    unique_wallet_24h = (h24_tx.get("buyers") or 0) + (h24_tx.get("sellers") or 0)

    return PoolListing(
        pool_address=attrs.get("address", ""),
        token_address=_strip_network_prefix(token_id, network),
        symbol=symbol or "???",
        created_at=attrs.get("pool_created_at", ""),
        price_usd=_as_float(attrs.get("base_token_price_usd")),
        liquidity_usd=_as_float(attrs.get("reserve_in_usd")),
        volume_24h_usd=_as_float((attrs.get("volume_usd") or {}).get("h24")),
        trade_24h=trade_24h,
        unique_wallet_24h=unique_wallet_24h,
        dex=(relationships.get("dex", {}).get("data") or {}).get("id"),
        locked_liquidity_percentage=_as_float(attrs.get("locked_liquidity_percentage")),
    )


class GeckoTerminalClient:
    def __init__(self, network: str = "solana") -> None:
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})
        self._network = network

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{GECKOTERMINAL_BASE_URL}{path}"

        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self._session.get(url, params=params or {}, timeout=REQUEST_TIMEOUT_SECONDS)
            except requests.RequestException as exc:
                last_error = exc
            else:
                if response.status_code == 200:
                    return response.json()

                if response.status_code == 429 or response.status_code >= 500:
                    last_error = GeckoTerminalAPIError(
                        f"{path} -> HTTP {response.status_code}: {response.text[:200]}"
                    )
                else:
                    raise GeckoTerminalAPIError(
                        f"{path} -> HTTP {response.status_code}: {response.text[:200]}"
                    )

            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)

        raise GeckoTerminalAPIError(f"{path} nach {MAX_RETRIES} Versuchen fehlgeschlagen: {last_error}")

    def get_new_pools(self, page: int = 1) -> list[dict[str, Any]]:
        """Neueste Pools (inkl. Bonding-Curve-Launches wie pump.fun/Launchlab).
        20 Einträge pro Seite (fest von GeckoTerminal vorgegeben)."""
        data = self._get(f"/networks/{self._network}/new_pools", {"page": page})
        return data.get("data", [])

    def get_pool(self, pool_address: str) -> dict[str, Any] | None:
        data = self._get(f"/networks/{self._network}/pools/{pool_address}")
        return data.get("data")

    def get_token_price(self, token_address: str) -> float | None:
        """Aktueller Preis eines einzelnen Tokens per Adresse (nicht Pool-
        Adresse) - genutzt von paper_trading.py, um offene Positionen zu
        prüfen, ohne den vollen Pool-Kontext erneut zu laden."""
        data = self._get(f"/networks/{self._network}/tokens/{token_address}")
        attrs = (data.get("data") or {}).get("attributes") or {}
        return _as_float(attrs.get("price_usd"))

    def get_ohlcv(
        self,
        pool_address: str,
        timeframe: str = "minute",
        aggregate: int = 1,
        before_timestamp: int | None = None,
        limit: int = 100,
    ) -> list[list[float]]:
        """[unix_time, open, high, low, close, volume] je Kerze, neueste zuerst."""
        params: dict[str, Any] = {"aggregate": aggregate, "limit": limit}
        if before_timestamp is not None:
            params["before_timestamp"] = before_timestamp
        data = self._get(f"/networks/{self._network}/pools/{pool_address}/ohlcv/{timeframe}", params)
        return data.get("data", {}).get("attributes", {}).get("ohlcv_list", [])

    def throttle(self) -> None:
        time.sleep(GECKOTERMINAL_THROTTLE_SECONDS)
