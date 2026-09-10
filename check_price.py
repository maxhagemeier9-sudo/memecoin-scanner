"""Manueller Check: Preis eines einzelnen Tokens abfragen."""
from birdeye_client import BirdeyeAPIError, BirdeyeClient

SOL_ADDRESS = "So11111111111111111111111111111111111111112"


def main() -> None:
    client = BirdeyeClient()
    try:
        price_data = client.get_price(SOL_ADDRESS)
    except BirdeyeAPIError as exc:
        print(f"Fehler: {exc}")
        return

    print(price_data)


if __name__ == "__main__":
    main()
