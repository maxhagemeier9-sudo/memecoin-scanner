"""Zentrale Konfiguration für den Memecoin-Scanner."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} wurde nicht in der .env gefunden.")
    return value


BIRDEYE_API_KEY = _get_required_env("BIRDEYE_API_KEY")
BIRDEYE_BASE_URL = "https://public-api.birdeye.so"
DEFAULT_CHAIN = "solana"

REQUEST_TIMEOUT_SECONDS = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2.0


@dataclass(frozen=True)
class FilterCriteria:
    min_market_cap: float = 50_000
    max_market_cap: float = 50_000_000
    min_volume_24h: float = 100_000


DEFAULT_FILTER_CRITERIA = FilterCriteria()

# Wie viele neue Coins pro Lauf angereichert werden (2 zusätzliche API-Calls je Coin)
NEW_COINS_LIMIT = 10
# Pause zwischen einzelnen Detail-Calls, um Rate-Limits (HTTP 429) zu vermeiden
NEW_COINS_THROTTLE_SECONDS = 0.4


@dataclass(frozen=True)
class RiskThresholds:
    # Holder-Konzentration (top10_hold_percent von Birdeye)
    top10_percent_high: float = 70.0
    top10_percent_medium: float = 50.0
    min_holder_count: int = 50
    # Liquidität/Handel
    min_liquidity_usd: float = 2_000
    max_volume_liquidity_ratio: float = 20.0
    max_trades_per_wallet: float = 15.0
    # Token-2022 Transfer-Steuer (transferFeeConfig), in Basispunkten (100 = 1%)
    transfer_fee_bps_high: int = 1000
    transfer_fee_bps_medium: int = 300
    # Serial-Launcher: wie viele ANDERE Coins in unserer lokalen Historie
    # bereits von derselben Creator-Wallet (Token-2022 updateAuthority) stammen
    serial_launcher_count_high: int = 5
    serial_launcher_count_medium: int = 2
    # Liquiditäts-Einbruch seit dem letzten Scan derselben Adresse, in Prozent
    liquidity_crash_percent_high: float = 50.0
    liquidity_crash_percent_medium: float = 20.0


DEFAULT_RISK_THRESHOLDS = RiskThresholds()

SOLANA_RPC_URL = "https://api.mainnet-beta.solana.com"

# Der Risk-Scan macht 3 Birdeye- + 1 Solana-RPC-Call pro Coin. 20 ist das
# Maximum, das der new_listing-Endpoint pro Aufruf erlaubt (HTTP 400 darüber).
RANKING_SCAN_LIMIT = 20
RISK_SCAN_THROTTLE_SECONDS = 0.5

# Wie viele Coins im Ranking angezeigt werden (sinnvoller Bereich laut
# Nutzer-Vorgabe: 5-20).
RANKING_TOP_N = 10


@dataclass(frozen=True)
class ScoreWeights:
    """Gewichtung der Score-Faktoren (0-100), muss in Summe 100 ergeben."""
    holder_concentration: int = 25
    liquidity: int = 20
    holder_count: int = 15
    volume_liquidity_ratio: int = 15
    wash_trading: int = 15
    transfer_fee: int = 10

    def __post_init__(self) -> None:
        total = (
            self.holder_concentration
            + self.liquidity
            + self.holder_count
            + self.volume_liquidity_ratio
            + self.wash_trading
            + self.transfer_fee
        )
        if total != 100:
            raise ValueError(f"ScoreWeights müssen in Summe 100 ergeben, sind aber {total}")


DEFAULT_SCORE_WEIGHTS = ScoreWeights()

# Score wird auf diesen Wert gedeckelt, sobald mindestens ein KRITISCH-Risk-
# Finding vorliegt (Mint-/Freeze-Authority aktiv, TransferHook,
# PermanentDelegate, NonTransferable) - unabhängig von allen anderen Faktoren.
CRITICAL_SCORE_CAP = 10

# Lokale Historie aller gescannten Coins (SQLite) - Grundlage für
# Trend-Signale (z.B. einbrechende Liquidität) und Creator-Reputation.
HISTORY_DB_PATH = Path(__file__).parent / "history.sqlite3"

# Export der Ranking-Ergebnisse als CSV/JSON nach jedem Lauf
EXPORT_RESULTS = True
EXPORT_DIR = Path(__file__).parent / "exports"

# Monitor-Modus (monitor.py): Dauerlauf mit Alert bei Score-Schwelle
MONITOR_INTERVAL_SECONDS = 120
MONITOR_ALERT_SCORE_THRESHOLD = 70

# Telegram-Alerts - optional. Wenn beide Werte fehlen, bleibt Telegram
# einfach deaktiviert (siehe telegram_alerts.py), kein Crash.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
