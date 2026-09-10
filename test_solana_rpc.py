"""Tests für das Extension-Parsing - laufen offline mit echten, live
erfassten RPC-Response-Shapes (siehe solana_rpc.py Docstring)."""
from unittest.mock import Mock, patch

import pytest
import requests

from solana_rpc import SolanaRPCError, _parse_extensions, get_mint_authorities

TRANSFER_FEE_INFO = {
    "extensions": [
        {"extension": "metadataPointer", "state": {"authority": None, "metadataAddress": "abc"}},
        {
            "extension": "transferFeeConfig",
            "state": {
                "newerTransferFee": {"epoch": 1031, "maximumFee": 1_000_000_000_000_000, "transferFeeBasisPoints": 100},
                "olderTransferFee": {"epoch": 1031, "maximumFee": 1_000_000_000_000_000, "transferFeeBasisPoints": 100},
                "transferFeeConfigAuthority": "abc",
                "withdrawWithheldAuthority": "def",
                "withheldAmount": 0,
            },
        },
        {
            "extension": "tokenMetadata",
            "state": {"name": "Test", "symbol": "TST", "updateAuthority": "CreatorWalletAddr"},
        },
    ]
}

NO_EXTENSIONS_INFO = {"mintAuthority": None, "freezeAuthority": None}

TRANSFER_HOOK_INFO = {
    "extensions": [
        {"extension": "transferHookAccount", "state": {}},
        {"extension": "transferHook", "state": {"authority": "abc", "programId": "def"}},
    ]
}

NULL_UPDATE_AUTHORITY_INFO = {
    "extensions": [
        {"extension": "tokenMetadata", "state": {"name": "Anon", "symbol": "anon", "updateAuthority": None}},
    ]
}


def test_transfer_fee_config_is_parsed_with_basis_points():
    names, bps, update_authority = _parse_extensions(TRANSFER_FEE_INFO)
    assert "transferFeeConfig" in names
    assert "metadataPointer" in names
    assert bps == 100
    assert update_authority == "CreatorWalletAddr"


def test_missing_extensions_key_yields_empty_result():
    names, bps, update_authority = _parse_extensions(NO_EXTENSIONS_INFO)
    assert names == frozenset()
    assert bps is None
    assert update_authority is None


def test_transfer_hook_extension_is_detected():
    names, bps, update_authority = _parse_extensions(TRANSFER_HOOK_INFO)
    assert "transferHook" in names
    assert bps is None
    assert update_authority is None


def test_null_update_authority_is_parsed_as_none():
    names, bps, update_authority = _parse_extensions(NULL_UPDATE_AUTHORITY_INFO)
    assert "tokenMetadata" in names
    assert update_authority is None


def _rpc_response(status_code=200, json_data=None):
    resp = Mock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = ""
    return resp


@patch("solana_rpc.requests.post")
def test_get_mint_authorities_parses_successful_response(mock_post):
    mock_post.return_value = _rpc_response(200, {
        "result": {
            "value": {
                "data": {"parsed": {"info": {
                    "mintAuthority": None,
                    "freezeAuthority": None,
                    "extensions": [{"extension": "tokenMetadata", "state": {"updateAuthority": "Creator"}}],
                }}}
            }
        }
    })
    result = get_mint_authorities("SomeMintAddress")
    assert result.mint_authority is None
    assert result.freeze_authority is None
    assert result.update_authority == "Creator"


@patch("solana_rpc.requests.post")
def test_get_mint_authorities_raises_on_rpc_error(mock_post):
    mock_post.return_value = _rpc_response(200, {"error": {"code": -1, "message": "bad request"}})
    with pytest.raises(SolanaRPCError):
        get_mint_authorities("SomeMintAddress")


@patch("solana_rpc.requests.post")
def test_get_mint_authorities_raises_when_account_missing(mock_post):
    mock_post.return_value = _rpc_response(200, {"result": {"value": None}})
    with pytest.raises(SolanaRPCError):
        get_mint_authorities("SomeMintAddress")


@patch("solana_rpc.requests.post")
def test_get_mint_authorities_raises_on_http_error(mock_post):
    mock_post.return_value = _rpc_response(500, {})
    with pytest.raises(SolanaRPCError):
        get_mint_authorities("SomeMintAddress")


@patch("solana_rpc.requests.post")
def test_get_mint_authorities_raises_on_network_exception(mock_post):
    mock_post.side_effect = requests.ConnectionError("boom")
    with pytest.raises(SolanaRPCError):
        get_mint_authorities("SomeMintAddress")
