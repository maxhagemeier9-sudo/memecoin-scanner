# Memecoin Risk Scanner

Scannt neu gelistete Solana-Token über die [Birdeye](https://docs.birdeye.so/)-API, bewertet sie
auf gängige Betrugs-/Risikosignale und verdichtet alles zu einem 0-100-Score pro Coin. Läuft
periodisch über GitHub Actions und schickt Alerts per Telegram.

**Wichtig:** Das ist ein marktbasierter Heuristik-Check, kein vollständiger Security-Audit und
keine Anlageberatung. Siehe [Was geprüft wird](#was-geprüft-wird) für die bewussten Grenzen.

## Module

| Datei | Zweck |
|---|---|
| `birdeye_client.py` | Dünner Client für die Birdeye-API (Retry/Backoff bei Rate-Limits) |
| `solana_rpc.py` | Mint-/Freeze-Authority + Token-2022-Extensions direkt per Solana-RPC |
| `filters.py` / `scanner.py` / `main.py` | Allgemeiner Scan über die Top-Token-Liste, gefiltert nach Market-Cap/Volumen |
| `new_coins.py` | Findet neu gelistete Coins (Liquidität, Volumen, Alter, Holder) |
| `risk.py` | Reine Risiko-Bewertungslogik (Findings aus Rohdaten) |
| `score.py` | Verdichtet Risk-Findings + Marktdaten zu einem 0-100-Score |
| `risk_scan.py` | Orchestrierung: scannt, bewertet, rankt, zeigt Top N |
| `history.py` | Lokale SQLite-Historie - Grundlage für Trend-/Creator-Signale |
| `alerts.py` / `telegram_alerts.py` | Alert-Versand (Beep + Telegram) |
| `monitor.py` | Dauerlauf-Modus (lokal) |
| `ci_scan.py` | Einzelner Scan-Zyklus für zeitgesteuerte Trigger (GitHub Actions) |
| `export.py` | CSV/JSON-Export der Ranking-Ergebnisse |

## Was geprüft wird

**Zuverlässig, mit den hier verfügbaren Datenquellen:**
- Mint-/Freeze-Authority (on-chain, per Solana-RPC) - kann der Ersteller neue Token erzeugen oder Wallets einfrieren?
- Token-2022-Extensions: TransferHook, PermanentDelegate, NonTransferable, Transfer-Steuer
- Holder-Konzentration (Top-10-%, Gesamtzahl)
- Liquidität, Volumen/Liquidität-Verhältnis, Wash-Trading-Muster (Trades/Wallet)
- Liquiditäts-Trend seit dem letzten Scan derselben Adresse
- Creator-Reputation: wie viele andere Coins dieselbe Creator-Wallet schon gelistet hat ("Serial-Launcher")

**Bewusst NICHT geprüft:**
- Liquiditäts-Sperren/-Burns (die meisten frischen Coins laufen noch auf einer Bonding Curve ohne klassischen LP-Token)
- Tatsächliche Ausführung von Transfer-Hook-Logik (nur ob die Extension aktiv ist, nicht was sie tut)
- Contract-Level-Honeypot-Simulation (bräuchte eine echte Swap-Transaktion)

## Lokal einrichten

```bash
pip install -r requirements.txt
cp .env.example .env  # dann API-Keys eintragen
python -m pytest       # 105 Tests, alle offline außer keine (Mocks)
python risk_scan.py    # einmaliger Scan + Ranking
python monitor.py      # Dauerlauf mit Telegram-Alerts
```

## GitHub Actions (automatischer Betrieb)

`.github/workflows/scan.yml` scannt alle 10 Minuten automatisch, unabhängig vom eigenen Rechner.
Benötigt drei Repository-Secrets (Settings → Secrets and variables → Actions):
`BIRDEYE_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

Die Historie-DB (`history.sqlite3`) wird zwischen den Läufen per `actions/cache` erhalten, damit
Trend- und Creator-Signale auch über einzelne, unabhängige Workflow-Läufe hinweg funktionieren.
