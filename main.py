"""Einstiegspunkt: Scan ausführen und Ergebnisse ausgeben."""
from __future__ import annotations

from birdeye_client import BirdeyeAPIError, BirdeyeClient
from filters import FilterResult
from scanner import scan


def _print_results(results: list[FilterResult]) -> None:
    passed = [r for r in results if r.passed]
    rejected = [r for r in results if not r.passed]

    for r in rejected:
        print(f"❌ {r.symbol:12} | {r.reason}")

    print(f"\n--- {len(passed)} von {len(results)} Tokens erfüllen die Kriterien ---\n")

    for r in passed:
        print(
            f"✅ {r.symbol:12} | {r.name[:25]:25} | "
            f"Market Cap: ${r.market_cap:,.0f} | Volumen: ${r.volume_24h:,.0f}"
        )


def main() -> None:
    client = BirdeyeClient()
    try:
        results = scan(client)
    except BirdeyeAPIError as exc:
        print(f"Scan fehlgeschlagen: {exc}")
        return

    _print_results(results)


if __name__ == "__main__":
    main()
