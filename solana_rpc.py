"""Direkter Solana-RPC-Client für Mint-/Freeze-Authority und Token-2022-
Extensions (TransferHook, PermanentDelegate, NonTransferable, Transfer-Fee).

Läuft unabhängig von Birdeye: der Birdeye-Security-Endpoint
(`/defi/token_security`), der diese Werte normalerweise liefert, gibt mit
dem aktuellen API-Plan HTTP 401 ("lacks sufficient permissions") zurück.
Alle hier geprüften Werte stehen aber direkt im SPL-Token-Mint-Account auf
der Chain und sind über jeden öffentlichen Solana-RPC-Knoten kostenlos
abrufbar - ohne Birdeye-API-Key. Live verifiziert: das Token-2022-Programm
(TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb) liefert bei jsonParsed eine
`extensions`-Liste mit Einträgen wie {"extension": "transferFeeConfig", ...}.
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

    return MintAuthorities(
        mint_authority=info.get("mintAuthority"),
        freeze_authority=info.get("freezeAuthority"),
        extensions=extensions,
        transfer_fee_basis_points=transfer_fee_bps,
        update_authority=update_authority,
    )
