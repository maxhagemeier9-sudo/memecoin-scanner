"""Direkter Solana-RPC-Client für Mint-/Freeze-Authority, Token-2022-
Extensions (TransferHook, PermanentDelegate, NonTransferable, Transfer-Fee)
und Holder-Konzentration.

Läuft über Helius (config.SOLANA_RPC_URL) statt dem öffentlichen
api.mainnet-beta.solana.com - der drosselt insbesondere
getTokenLargestAccounts stark (wiederholt HTTP 429 "Too many requests for a
specific RPC call" in Tests, live verifiziert), Helius' Free-Tier nicht.
Alle hier geprüften Werte stehen direkt im SPL-Token-Mint-Account auf der
Chain und sind damit unabhängig von Birdeye/GeckoTerminal. Live verifiziert:
das Token-2022-Programm (TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb) liefert
bei jsonParsed eine `extensions`-Liste mit Einträgen wie
{"extension": "transferFeeConfig", ...}.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import requests

from config import SOLANA_RPC_URL

REQUEST_TIMEOUT_SECONDS = 15


class SolanaRPCError(RuntimeError):
    """Wird ausgelöst, wenn der Solana-RPC-Knoten einen Fehler zurückgibt."""


@dataclass(frozen=True)
class MintAuthorities:
    mint_authority: str | None
    freeze_authority: str | None
    extensions: frozenset[str] = field(default_factory=frozenset)
    transfer_fee_basis_points: int | None = None
    # Token-2022 tokenMetadata.updateAuthority - dient als Näherung für die
    # "Creator-Wallet" (siehe history.py/risk.py evaluate_creator_history).
    # Nur für Token-2022-Mints verfügbar; klassische SPL-Token-Mints tragen
    # Creator-Infos nur in einem separaten Metaplex-Metadata-Account, den wir
    # hier bewusst nicht zusätzlich abfragen.
    update_authority: str | None = None
    # Gesamt-Supply als UI-Amount (bereits durch 10^decimals geteilt) - aus
    # demselben Call wie die Authorities, für get_top10_concentration.
    total_supply: float | None = None


def _parse_extensions(info: dict[str, Any]) -> tuple[frozenset[str], int | None, str | None]:
    """Reine Parsing-Logik, getrennt vom RPC-Call - offline testbar."""
    extensions_raw = info.get("extensions") or []
    names = frozenset(ext["extension"] for ext in extensions_raw if ext.get("extension"))

    transfer_fee_bps = None
    update_authority = None
    for ext in extensions_raw:
        ext_name = ext.get("extension")
        if ext_name == "transferFeeConfig":
            fee = ext.get("state", {}).get("newerTransferFee", {})
            transfer_fee_bps = fee.get("transferFeeBasisPoints")
        elif ext_name == "tokenMetadata":
            update_authority = ext.get("state", {}).get("updateAuthority")

    return names, transfer_fee_bps, update_authority


def get_mint_authorities(mint_address: str) -> MintAuthorities:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [mint_address, {"encoding": "jsonParsed"}],
    }

    try:
        response = requests.post(SOLANA_RPC_URL, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise SolanaRPCError(f"RPC-Aufruf fehlgeschlagen: {exc}") from exc

    if response.status_code != 200:
        raise SolanaRPCError(f"RPC -> HTTP {response.status_code}: {response.text[:200]}")

    body: dict[str, Any] = response.json()

    if "error" in body:
        raise SolanaRPCError(f"RPC-Fehler: {body['error']}")

    value = (body.get("result") or {}).get("value")
    if value is None:
        raise SolanaRPCError(f"Kein Account gefunden für {mint_address}")

    info = value.get("data", {}).get("parsed", {}).get("info", {})
    if not info:
        raise SolanaRPCError(f"Account {mint_address} ist kein geparster SPL-Token-Mint")

    extensions, transfer_fee_bps, update_authority = _parse_extensions(info)
    total_supply = _parse_total_supply(info)

    return MintAuthorities(
        mint_authority=info.get("mintAuthority"),
        freeze_authority=info.get("freezeAuthority"),
        extensions=extensions,
        transfer_fee_basis_points=transfer_fee_bps,
        update_authority=update_authority,
        total_supply=total_supply,
    )


def _parse_total_supply(info: dict[str, Any]) -> float | None:
    """Reine Parsing-Logik, offline testbar: rohe supply + decimals -> UI-Amount."""
    supply_raw = info.get("supply")
    decimals = info.get("decimals")
    if supply_raw is None or decimals is None:
        return None
    try:
        return float(supply_raw) / (10 ** decimals)
    except (TypeError, ValueError):
        return None


def get_top10_concentration(mint_address: str, total_supply: float | None) -> float | None:
    """Top-10-Holder-Konzentration in Prozent, berechnet aus den 10 größten
    Einträgen von getTokenLargestAccounts (liefert bis zu 20 Token-Accounts,
    absteigend sortiert) geteilt durch die Gesamt-Supply. None (kein Fehler),
    wenn total_supply fehlt oder 0 ist - dafür gibt es keinen Recovery-Pfad.
    Echte RPC-Fehler werden als SolanaRPCError durchgereicht, damit der
    Aufrufer sie wie die anderen RPC-Calls behandeln kann (siehe
    risk_scan.assess_token).
    """
    if not total_supply or total_supply <= 0:
        return None

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenLargestAccounts",
        "params": [mint_address],
    }

    try:
        response = requests.post(SOLANA_RPC_URL, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise SolanaRPCError(f"RPC-Aufruf fehlgeschlagen: {exc}") from exc

    if response.status_code != 200:
        raise SolanaRPCError(f"RPC -> HTTP {response.status_code}: {response.text[:200]}")

    body: dict[str, Any] = response.json()

    if "error" in body:
        raise SolanaRPCError(f"RPC-Fehler: {body['error']}")

    accounts = (body.get("result") or {}).get("value") or []
    top10_amount = sum(acc.get("uiAmount") or 0 for acc in accounts[:10])

    return min(top10_amount / total_supply * 100, 100.0)
