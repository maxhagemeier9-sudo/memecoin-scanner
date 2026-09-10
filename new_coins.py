"""Findet neu gelistete Solana-Token und reichert sie mit Liquidität,
Volumen, Alter und Holder-Anzahl an.

Pro Coin sind zwei zusätzliche API-Calls nötig (market-data + trade-data),
da Birdeye diese Werte nicht in einem einzigen Endpoint zusammenfasst.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

from birdeye_client import BirdeyeAPIError, BirdeyeClient
from config import NEW_COINS_LIMIT, NEW_COINS_THROTTLE_SECONDS


@dataclass(frozen=True)
class NewCoinInfo:
    address: str
    symbol: str
    name: str
    liquidity_usd: float
    volume_24h_usd: float
    holders: int
    age_minutes: float


def age_minutes(listed_at: str, now: datetime | None = None) -> float:
    """Minuten seit `listed_at` (ISO-Format, wie von Birdeye geliefert, UTC)."""
    listed_time = datetime.fromisoformat(listed_at).replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    return (reference - listed_time).total_seconds() / 60


def format_age(minutes: float) -> str:
    if minutes < 60:
        return f"{minutes:.0f} Min"
    if minutes < 24 * 60:
        return f"{minutes / 60:.1f} Std"
    return f"{minutes / (24 * 60):.1f} Tage"


def fetch_new_coins(
    client: BirdeyeClient,
    limit: int = NEW_COINS_LIMIT,
) -> tuple[list[NewCoinInfo], int]:
    """Gibt (angereicherte Coins, Anzahl übersprungener Coins) zurück.

    Coins, für die die Detaildaten nicht abgerufen werden konnten (z.B.
    einzelner Rate-Limit-Fehler trotz Retry), werden übersprungen statt den
    ganzen Lauf abzubrechen.
    """
    listings = client.get_new_listings(limit=limit)

    results: list[NewCoinInfo] = []
    skipped = 0

    for listing in listings:
        address = listing["address"]

        try:
            market_data = client.get_market_data(address)
            time.sleep(NEW_COINS_THROTTLE_SECONDS)
            trade_data = client.get_trade_data(address)
            time.sleep(NEW_COINS_THROTTLE_SECONDS)
        except BirdeyeAPIError:
            skipped += 1
            continue

        results.append(
            NewCoinInfo(
                address=address,
                symbol=listing.get("symbol") or "???",
                name=listing.get("name") or "Unknown",
                liquidity_usd=market_data.get("liquidity") or listing.get("liquidity") or 0,
                volume_24h_usd=trade_data.get("volume_24h_usd") or 0,
                holders=market_data.get("holder") or 0,
                age_minutes=age_minutes(listing["liquidityAddedAt"]),
            )
        )

    return results, skipped


def _print_results(coins: list[NewCoinInfo], skipped: int) -> None:
    coins_by_age = sorted(coins, key=lambda c: c.age_minutes)

    header = f"{'Symbol':10} | {'Alter':>8} | {'Liquidität':>14} | {'Volumen 24h':>14} | {'Holder':>7}"
    print(header)
    print("-" * len(header))

    for c in coins_by_age:
        print(
            f"{c.symbol[:10]:10} | {format_age(c.age_minutes):>8} | "
            f"${c.liquidity_usd:>13,.0f} | ${c.volume_24h_usd:>13,.0f} | {c.holders:>7}"
        )

    print(f"\n{len(coins)} Coins angezeigt, {skipped} wegen API-Fehlern übersprungen.")


def main() -> None:
    client = BirdeyeClient()
    try:
        coins, skipped = fetch_new_coins(client)
    except BirdeyeAPIError as exc:
        print(f"Abruf fehlgeschlagen: {exc}")
        return

    _print_results(coins, skipped)


if __name__ == "__main__":
    main()
