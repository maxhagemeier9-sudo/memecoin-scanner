# Memecoin Risk Scanner

Scannt neu gelistete Solana-Token, bewertet sie auf gängige Betrugs-/Risikosignale und
verdichtet alles zu einem 0-100-Score pro Coin. Läuft periodisch über GitHub Actions und
schickt Alerts + Zusammenfassungen per Telegram. Zusätzlich: Backtesting gegen echte
Kursverläufe und Paper Trading, um zu messen, ob die Signale tatsächlich profitabel wären.

**Wichtig:** Das ist ein marktbasierter Heuristik-Check, kein vollständiger Security-Audit und
keine Anlageberatung. Siehe [Was geprüft wird](#was-geprüft-wird) für die bewussten Grenzen.

## Datenquellen

Seit 2026-09-10 (Birdeye-Compute-Units-Kontingent bis 2026-10-08 erschöpft):

| Quelle | Wofür | Kosten |
|---|---|---|
| [GeckoTerminal](https://www.geckoterminal.com/dex-api) (CoinGecko On-Chain API) | Discovery neuer Pools, Preis/Liquidität/Volumen/Handelszahlen, OHLCV | Kostenlos, kein API-Key |
| [Helius](https://www.helius.dev/) | Solana-RPC (Mint-/Freeze-Authority, Token-2022-Extensions, Holder-Konzentration) | Kostenlos (API-Key nötig) |
| Birdeye | *(dormant, siehe `birdeye_client.py`/`filters.py`/`scanner.py`/`new_coins.py`)* | Kontingent erschöpft bis 2026-10-08 |

## Module

| Datei | Zweck |
|---|---|
| `geckoterminal_client.py` | Client für GeckoTerminal (Discovery, Preis/Liquidität/Volumen, OHLCV) |
| `solana_rpc.py` | Mint-/Freeze-Authority, Token-2022-Extensions, Holder-Konzentration per Helius-RPC |
| `risk.py` | Reine Risiko-Bewertungslogik (Findings aus Rohdaten) |
| `score.py` | Verdichtet Risk-Findings + Marktdaten zu einem 0-100-Score |
| `risk_scan.py` | Orchestrierung: scannt, bewertet, rankt, zeigt Top N |
| `history.py` | Lokale SQLite-Historie - Grundlage für Trend-/Creator-Signale, Backtesting, Paper Trading |
| `backtest.py` | Vergleicht Score bei Erstsichtung mit echter Kursentwicklung (OHLCV) |
| `paper_trading.py` | Simuliert Trades anhand der eigenen Signale, protokolliert P&L |
| `alerts.py` / `telegram_alerts.py` | Alert-Versand (Beep + Telegram) |
| `monitor.py` | Dauerlauf-Modus (lokal), eröffnet/schließt Paper-Trades |
| `ci_scan.py` | Einzelner Scan-Zyklus für zeitgesteuerte Trigger (GitHub Actions) |
| `export.py` | CSV/JSON-Export der Ranking-Ergebnisse |
| `birdeye_client.py` / `filters.py` / `scanner.py` / `main.py` / `new_coins.py` | Dormant seit Kontingent-Erschöpfung, funktioniert wieder ab 2026-10-08 |

## Was geprüft wird

**Zuverlässig, mit den hier verfügbaren Datenquellen:**
- Mint-/Freeze-Authority (on-chain, per Helius-RPC) - kann der Ersteller neue Token erzeugen oder Wallets einfrieren?
- Token-2022-Extensions: TransferHook, PermanentDelegate, NonTransferable, Transfer-Steuer
- Holder-Konzentration (Top-10-%, aus `getTokenLargestAccounts` + Supply selbst berechnet)
- Liquidität, Volumen/Liquidität-Verhältnis, Wash-Trading-Muster (Trades/Wallet)
- Liquiditäts-Trend seit dem letzten Scan derselben Adresse
- Creator-Reputation: wie viele andere Coins dieselbe Creator-Wallet schon gelistet hat ("Serial-Launcher")

**Bewusst NICHT (mehr) geprüft:**
- Gesamt-Holder-Zahl (nur noch Top-10-Konzentration - `getTokenLargestAccounts` liefert nur die
  größten 20 Accounts, keine Gesamtzahl; der Score-Faktor ist auf Gewicht 0 gesetzt statt entfernt)
- Liquiditäts-Sperren/-Burns (die meisten frischen Coins laufen noch auf einer Bonding Curve ohne klassischen LP-Token)
- Tatsächliche Ausführung von Transfer-Hook-Logik (nur ob die Extension aktiv ist, nicht was sie tut)
- Contract-Level-Honeypot-Simulation (bräuchte eine echte Swap-Transaktion)

## Lokal einrichten

```bash
pip install -r requirements.txt
cp .env.example .env  # dann API-Keys eintragen (HELIUS_API_KEY erforderlich)
python -m pytest       # alle Tests offline/gemockt
python risk_scan.py    # einmaliger Scan + Ranking
python monitor.py      # Dauerlauf mit Telegram-Alerts + Paper Trading
python backtest.py     # Score vs. echte Kursentwicklung
python paper_trading.py  # aktueller Stand der simulierten Positionen
```

## GitHub Actions (automatischer Betrieb)

`.github/workflows/scan.yml` scannt alle 10 Minuten automatisch, unabhängig vom eigenen Rechner.
Benötigt Repository-Secrets (Settings → Secrets and variables → Actions):
`HELIUS_API_KEY` (erforderlich), `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (optional, für Alerts),
`BIRDEYE_API_KEY` (optional, für den dormanten Birdeye-Pfad).

Die Historie-DB (`history.sqlite3`) wird zwischen den Läufen per `actions/cache` erhalten, damit
Trend- und Creator-Signale sowie offene Paper-Trades auch über einzelne, unabhängige
Workflow-Läufe hinweg funktionieren.
