"""Manueller Check: Trade-Daten eines einzelnen Tokens abfragen."""
from birdeye_client import BirdeyeAPIError, BirdeyeClient

TOKEN_ADDRESS = "8p9YFehQSJ1QWoVar6CjmXBkCb1ft2RiquT5m8weGUcH"


def main() -> None:
    client = BirdeyeClient()
    try:
        trade_data = client.get_trade_data(TOKEN_ADDRESS)
    except BirdeyeAPIError as exc:
        print(f"Fehler: {exc}")
        return

    print(trade_data)


if __name__ == "__main__":
    main()
