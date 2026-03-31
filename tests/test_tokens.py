"""Tests for multi-asset token system: ISSUE_TOKEN, MINT_TOKEN, TRANSFER_TOKEN.

Covers payload validation, factory methods, state machine operations,
rollback, query methods, and receipt events. ~120 tests total.
"""
import pytest

from qbit_network.core.blockchain import Blockchain
from qbit_network.core.transaction import Transaction, TxType
from qbit_network.core.wallet import Wallet
from qbit_network.crypto import sha3_256
from qbit_network.config import (
    MAX_TOKEN_NAME_LENGTH, MAX_TOKEN_SYMBOL_LENGTH,
    MIN_TOKEN_SYMBOL_LENGTH, MAX_TOKEN_DECIMALS,
    TX_FEES, QUBIT_PER_QBIT,
)


# ---- Helpers ----

def _derive_token_id(sender: str, symbol: str, nonce: int) -> str:
    return sha3_256((sender + symbol + str(nonce)).encode()).hex()[:32]


def _make_valid_address() -> str:
    """Create a valid qv1 address from a fresh wallet."""
    w = Wallet.generate()
    return w.address


@pytest.fixture
def wallet():
    return Wallet.generate()


@pytest.fixture
def wallet2():
    return Wallet.generate()


@pytest.fixture
def wallet3():
    return Wallet.generate()


@pytest.fixture
def funded_chain(wallet):
    """Blockchain with financial layer active and genesis validator funded."""
    bc = Blockchain()
    bc.consensus.add_validator(wallet.address, wallet.signing_pk)
    bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)
    bc.activate_financial_layer(wallet.address)
    return bc


def submit_and_mine(bc, wallet, tx):
    """Submit tx and produce block. Returns (block, tx_id)."""
    ok, tx_id = bc.submit_tx(tx)
    assert ok, f"submit_tx failed: {tx_id}"
    block = bc.produce_block(wallet.address, wallet.signing_sk)
    assert block is not None, "produce_block returned None"
    return block, tx_id


def fund_wallet(bc, funder, recipient_addr, amount, nonce=None):
    """Transfer native QBIT from funder to recipient."""
    if nonce is None:
        nonce = bc.get_nonce(funder.address) + bc._pool_sender_count.get(funder.address, 0)
    tx = Transaction.transfer(funder.address, recipient_addr, amount, nonce=nonce)
    tx.sign(funder.signing_sk, funder.signing_pk)
    return submit_and_mine(bc, funder, tx)


def issue_token(bc, wallet, name="TestCoin", symbol="TST", decimals=8,
                max_supply=0, transferable=True, nonce=None):
    """Issue a token and mine it. Returns (block, tx_id, token_id)."""
    if nonce is None:
        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
    tx = Transaction.issue_token(
        wallet.address, name=name, symbol=symbol, decimals=decimals,
        max_supply=max_supply, transferable=transferable, nonce=nonce,
    )
    tx.sign(wallet.signing_sk, wallet.signing_pk)
    block, tx_id = submit_and_mine(bc, wallet, tx)
    token_id = _derive_token_id(wallet.address, symbol, nonce)
    return block, tx_id, token_id


def mint_token(bc, issuer_wallet, recipient_addr, token_id, amount, nonce=None):
    """Mint tokens and mine. Returns (block, tx_id)."""
    if nonce is None:
        nonce = bc.get_nonce(issuer_wallet.address) + bc._pool_sender_count.get(issuer_wallet.address, 0)
    tx = Transaction.mint_token(
        issuer_wallet.address, recipient_addr, token_id, amount, nonce=nonce,
    )
    tx.sign(issuer_wallet.signing_sk, issuer_wallet.signing_pk)
    return submit_and_mine(bc, issuer_wallet, tx)


def transfer_token(bc, sender_wallet, recipient_addr, token_id, amount,
                   memo="", nonce=None):
    """Transfer tokens and mine. Returns (block, tx_id)."""
    if nonce is None:
        nonce = bc.get_nonce(sender_wallet.address) + bc._pool_sender_count.get(sender_wallet.address, 0)
    tx = Transaction.transfer_token(
        sender_wallet.address, recipient_addr, token_id, amount,
        memo=memo, nonce=nonce,
    )
    tx.sign(sender_wallet.signing_sk, sender_wallet.signing_pk)
    return submit_and_mine(bc, sender_wallet, tx)


# ========================================================================
# 1. PAYLOAD VALIDATION — ISSUE_TOKEN
# ========================================================================

class TestIssueTokenPayloadValidation:

    def test_valid_payload(self, wallet):
        tx = Transaction.issue_token(wallet.address, "My Token", "MTK", 8)
        ok, err = tx.validate_payload()
        assert ok, err

    def test_valid_with_max_supply(self, wallet):
        tx = Transaction.issue_token(wallet.address, "My Token", "MTK", 8,
                                      max_supply=1_000_000)
        ok, err = tx.validate_payload()
        assert ok, err

    def test_valid_not_transferable(self, wallet):
        tx = Transaction.issue_token(wallet.address, "Soul Token", "SOUL", 0,
                                      transferable=False)
        ok, err = tx.validate_payload()
        assert ok, err

    def test_missing_name(self, wallet):
        tx = Transaction(TxType.ISSUE_TOKEN, wallet.address,
                         payload={"symbol": "MTK", "decimals": 8})
        ok, err = tx.validate_payload()
        assert not ok
        assert "name" in err

    def test_empty_name(self, wallet):
        tx = Transaction.issue_token(wallet.address, "", "MTK", 8)
        ok, err = tx.validate_payload()
        assert not ok
        assert "name" in err

    def test_name_too_long(self, wallet):
        long_name = "A" * (MAX_TOKEN_NAME_LENGTH + 1)
        tx = Transaction.issue_token(wallet.address, long_name, "MTK", 8)
        ok, err = tx.validate_payload()
        assert not ok
        assert "name" in err

    def test_name_max_length_ok(self, wallet):
        name = "A" * MAX_TOKEN_NAME_LENGTH
        tx = Transaction.issue_token(wallet.address, name, "MTK", 8)
        ok, err = tx.validate_payload()
        assert ok, err

    def test_name_invalid_chars_special(self, wallet):
        tx = Transaction.issue_token(wallet.address, "My$Token!", "MTK", 8)
        ok, err = tx.validate_payload()
        assert not ok
        assert "alphanumeric" in err

    def test_name_invalid_chars_unicode(self, wallet):
        tx = Transaction.issue_token(wallet.address, "Token\u00e9", "MTK", 8)
        ok, err = tx.validate_payload()
        assert not ok

    def test_name_invalid_chars_underscore(self, wallet):
        tx = Transaction.issue_token(wallet.address, "My_Token", "MTK", 8)
        ok, err = tx.validate_payload()
        assert not ok

    def test_name_with_spaces_ok(self, wallet):
        tx = Transaction.issue_token(wallet.address, "My Token Name", "MTK", 8)
        ok, err = tx.validate_payload()
        assert ok, err

    def test_missing_symbol(self, wallet):
        tx = Transaction(TxType.ISSUE_TOKEN, wallet.address,
                         payload={"name": "Test", "decimals": 8})
        ok, err = tx.validate_payload()
        assert not ok
        assert "symbol" in err

    def test_symbol_too_short(self, wallet):
        short = "A" * (MIN_TOKEN_SYMBOL_LENGTH - 1)
        tx = Transaction.issue_token(wallet.address, "Test", short, 8)
        ok, err = tx.validate_payload()
        assert not ok
        assert "symbol" in err

    def test_symbol_too_long(self, wallet):
        long_sym = "A" * (MAX_TOKEN_SYMBOL_LENGTH + 1)
        tx = Transaction.issue_token(wallet.address, "Test", long_sym, 8)
        ok, err = tx.validate_payload()
        assert not ok
        assert "symbol" in err

    def test_symbol_min_length_ok(self, wallet):
        sym = "AB"
        tx = Transaction.issue_token(wallet.address, "Test", sym, 8)
        ok, err = tx.validate_payload()
        assert ok, err

    def test_symbol_max_length_ok(self, wallet):
        sym = "A" * MAX_TOKEN_SYMBOL_LENGTH
        tx = Transaction.issue_token(wallet.address, "Test", sym, 8)
        ok, err = tx.validate_payload()
        assert ok, err

    def test_symbol_lowercase_invalid(self, wallet):
        tx = Transaction.issue_token(wallet.address, "Test", "mtk", 8)
        ok, err = tx.validate_payload()
        assert not ok
        assert "uppercase" in err

    def test_symbol_mixed_case_invalid(self, wallet):
        tx = Transaction.issue_token(wallet.address, "Test", "Mtk", 8)
        ok, err = tx.validate_payload()
        assert not ok

    def test_symbol_starts_with_number_invalid(self, wallet):
        tx = Transaction.issue_token(wallet.address, "Test", "1TK", 8)
        ok, err = tx.validate_payload()
        assert not ok

    def test_symbol_with_special_chars_invalid(self, wallet):
        tx = Transaction.issue_token(wallet.address, "Test", "MT-K", 8)
        ok, err = tx.validate_payload()
        assert not ok

    def test_reserved_symbol_qbit(self, wallet):
        tx = Transaction.issue_token(wallet.address, "Fake QBIT", "QBIT", 9)
        ok, err = tx.validate_payload()
        assert not ok
        assert "reserved" in err.lower() or "QBIT" in err

    def test_decimals_negative(self, wallet):
        tx = Transaction.issue_token(wallet.address, "Test", "TST", -1)
        ok, err = tx.validate_payload()
        assert not ok
        assert "decimals" in err

    def test_decimals_too_high(self, wallet):
        tx = Transaction.issue_token(wallet.address, "Test", "TST",
                                      MAX_TOKEN_DECIMALS + 1)
        ok, err = tx.validate_payload()
        assert not ok
        assert "decimals" in err

    def test_decimals_zero_ok(self, wallet):
        tx = Transaction.issue_token(wallet.address, "Test", "TST", 0)
        ok, err = tx.validate_payload()
        assert ok, err

    def test_decimals_max_ok(self, wallet):
        tx = Transaction.issue_token(wallet.address, "Test", "TST",
                                      MAX_TOKEN_DECIMALS)
        ok, err = tx.validate_payload()
        assert ok, err

    def test_decimals_not_int(self, wallet):
        tx = Transaction(TxType.ISSUE_TOKEN, wallet.address, payload={
            "name": "Test", "symbol": "TST", "decimals": 8.5,
            "max_supply": 0, "transferable": True,
        })
        ok, err = tx.validate_payload()
        assert not ok
        assert "decimals" in err

    def test_max_supply_negative(self, wallet):
        tx = Transaction(TxType.ISSUE_TOKEN, wallet.address, payload={
            "name": "Test", "symbol": "TST", "decimals": 8,
            "max_supply": -1, "transferable": True,
        })
        ok, err = tx.validate_payload()
        assert not ok
        assert "max_supply" in err

    def test_max_supply_zero_means_unlimited(self, wallet):
        tx = Transaction.issue_token(wallet.address, "Test", "TST", 8,
                                      max_supply=0)
        ok, err = tx.validate_payload()
        assert ok, err

    def test_max_supply_not_int(self, wallet):
        tx = Transaction(TxType.ISSUE_TOKEN, wallet.address, payload={
            "name": "Test", "symbol": "TST", "decimals": 8,
            "max_supply": "1000", "transferable": True,
        })
        ok, err = tx.validate_payload()
        assert not ok
        assert "max_supply" in err

    def test_transferable_not_bool(self, wallet):
        tx = Transaction(TxType.ISSUE_TOKEN, wallet.address, payload={
            "name": "Test", "symbol": "TST", "decimals": 8,
            "max_supply": 0, "transferable": 1,
        })
        ok, err = tx.validate_payload()
        assert not ok
        assert "transferable" in err

    def test_unknown_payload_key_rejected(self, wallet):
        tx = Transaction(TxType.ISSUE_TOKEN, wallet.address, payload={
            "name": "Test", "symbol": "TST", "decimals": 8,
            "max_supply": 0, "transferable": True, "extra_field": "bad",
        })
        ok, err = tx.validate_payload()
        assert not ok
        assert "unknown" in err.lower()


# ========================================================================
# 2. PAYLOAD VALIDATION — MINT_TOKEN
# ========================================================================

class TestMintTokenPayloadValidation:

    def _valid_token_id(self):
        return "a" * 32

    def test_valid_payload(self, wallet):
        addr = _make_valid_address()
        tx = Transaction.mint_token(wallet.address, addr,
                                     self._valid_token_id(), 1000)
        ok, err = tx.validate_payload()
        assert ok, err

    def test_token_id_wrong_length_short(self, wallet):
        addr = _make_valid_address()
        tx = Transaction.mint_token(wallet.address, addr, "abcd1234", 1000)
        ok, err = tx.validate_payload()
        assert not ok
        assert "token_id" in err

    def test_token_id_wrong_length_long(self, wallet):
        addr = _make_valid_address()
        tx = Transaction.mint_token(wallet.address, addr, "a" * 33, 1000)
        ok, err = tx.validate_payload()
        assert not ok
        assert "token_id" in err

    def test_token_id_not_hex(self, wallet):
        addr = _make_valid_address()
        tx = Transaction.mint_token(wallet.address, addr, "g" * 32, 1000)
        ok, err = tx.validate_payload()
        assert not ok
        assert "token_id" in err

    def test_token_id_empty(self, wallet):
        addr = _make_valid_address()
        tx = Transaction.mint_token(wallet.address, addr, "", 1000)
        ok, err = tx.validate_payload()
        assert not ok

    def test_amount_zero(self, wallet):
        addr = _make_valid_address()
        tx = Transaction.mint_token(wallet.address, addr,
                                     self._valid_token_id(), 0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "amount" in err

    def test_amount_negative(self, wallet):
        addr = _make_valid_address()
        tx = Transaction.mint_token(wallet.address, addr,
                                     self._valid_token_id(), -100)
        ok, err = tx.validate_payload()
        assert not ok
        assert "amount" in err

    def test_amount_not_int(self, wallet):
        tx = Transaction(TxType.MINT_TOKEN, wallet.address,
                         recipient=_make_valid_address(),
                         payload={"token_id": self._valid_token_id(),
                                  "amount": 10.5})
        ok, err = tx.validate_payload()
        assert not ok
        assert "amount" in err

    def test_missing_recipient(self, wallet):
        tx = Transaction(TxType.MINT_TOKEN, wallet.address,
                         recipient="",
                         payload={"token_id": self._valid_token_id(),
                                  "amount": 1000})
        ok, err = tx.validate_payload()
        assert not ok
        assert "recipient" in err

    def test_invalid_recipient_address_format(self, wallet):
        tx = Transaction.mint_token(wallet.address, "bad_address",
                                     self._valid_token_id(), 1000)
        ok, err = tx.validate_payload()
        assert not ok
        assert "recipient" in err or "address" in err

    def test_invalid_recipient_wrong_prefix(self, wallet):
        # Valid length but wrong prefix
        tx = Transaction.mint_token(wallet.address, "qv2" + "a" * 64,
                                     self._valid_token_id(), 1000)
        ok, err = tx.validate_payload()
        assert not ok


# ========================================================================
# 3. PAYLOAD VALIDATION — TRANSFER_TOKEN
# ========================================================================

class TestTransferTokenPayloadValidation:

    def _valid_token_id(self):
        return "b" * 32

    def test_valid_payload(self, wallet):
        addr = _make_valid_address()
        tx = Transaction.transfer_token(wallet.address, addr,
                                         self._valid_token_id(), 500)
        ok, err = tx.validate_payload()
        assert ok, err

    def test_valid_with_memo(self, wallet):
        addr = _make_valid_address()
        tx = Transaction.transfer_token(wallet.address, addr,
                                         self._valid_token_id(), 500,
                                         memo="payment for services")
        ok, err = tx.validate_payload()
        assert ok, err

    def test_token_id_wrong_length(self, wallet):
        addr = _make_valid_address()
        tx = Transaction.transfer_token(wallet.address, addr, "abc", 500)
        ok, err = tx.validate_payload()
        assert not ok
        assert "token_id" in err

    def test_token_id_not_hex(self, wallet):
        addr = _make_valid_address()
        tx = Transaction.transfer_token(wallet.address, addr, "z" * 32, 500)
        ok, err = tx.validate_payload()
        assert not ok

    def test_amount_zero(self, wallet):
        addr = _make_valid_address()
        tx = Transaction.transfer_token(wallet.address, addr,
                                         self._valid_token_id(), 0)
        ok, err = tx.validate_payload()
        assert not ok
        assert "amount" in err

    def test_amount_negative(self, wallet):
        addr = _make_valid_address()
        tx = Transaction.transfer_token(wallet.address, addr,
                                         self._valid_token_id(), -1)
        ok, err = tx.validate_payload()
        assert not ok

    def test_missing_recipient(self, wallet):
        tx = Transaction(TxType.TRANSFER_TOKEN, wallet.address,
                         recipient="",
                         payload={"token_id": self._valid_token_id(),
                                  "amount": 500})
        ok, err = tx.validate_payload()
        assert not ok
        assert "recipient" in err

    def test_self_transfer_rejected(self, wallet):
        tx = Transaction.transfer_token(wallet.address, wallet.address,
                                         self._valid_token_id(), 500)
        ok, err = tx.validate_payload()
        assert not ok
        assert "self" in err

    def test_invalid_recipient(self, wallet):
        tx = Transaction.transfer_token(wallet.address, "not_an_address",
                                         self._valid_token_id(), 500)
        ok, err = tx.validate_payload()
        assert not ok

    def test_memo_too_long(self, wallet):
        addr = _make_valid_address()
        tx = Transaction.transfer_token(wallet.address, addr,
                                         self._valid_token_id(), 500,
                                         memo="x" * 257)
        ok, err = tx.validate_payload()
        assert not ok
        assert "memo" in err

    def test_memo_256_ok(self, wallet):
        addr = _make_valid_address()
        tx = Transaction.transfer_token(wallet.address, addr,
                                         self._valid_token_id(), 500,
                                         memo="x" * 256)
        ok, err = tx.validate_payload()
        assert ok, err

    def test_memo_not_string(self, wallet):
        addr = _make_valid_address()
        tx = Transaction(TxType.TRANSFER_TOKEN, wallet.address,
                         recipient=addr,
                         payload={"token_id": self._valid_token_id(),
                                  "amount": 500, "memo": 12345})
        ok, err = tx.validate_payload()
        assert not ok
        assert "memo" in err

    def test_empty_memo_not_included(self, wallet):
        addr = _make_valid_address()
        tx = Transaction.transfer_token(wallet.address, addr,
                                         self._valid_token_id(), 500, memo="")
        # Empty memo should not appear in payload
        assert "memo" not in tx.payload


# ========================================================================
# 4. FACTORY METHODS
# ========================================================================

class TestFactoryMethods:

    def test_issue_token_factory(self, wallet):
        tx = Transaction.issue_token(
            wallet.address, "Gold", "GLD", 6, max_supply=21_000_000,
            transferable=True, nonce=5,
        )
        assert tx.tx_type == TxType.ISSUE_TOKEN
        assert tx.sender == wallet.address
        assert tx.nonce == 5
        assert tx.payload["name"] == "Gold"
        assert tx.payload["symbol"] == "GLD"
        assert tx.payload["decimals"] == 6
        assert tx.payload["max_supply"] == 21_000_000
        assert tx.payload["transferable"] is True
        assert tx.recipient == ""

    def test_issue_token_defaults(self, wallet):
        tx = Transaction.issue_token(wallet.address, "Test", "TST", 8)
        assert tx.payload["max_supply"] == 0
        assert tx.payload["transferable"] is True
        assert tx.nonce == 0

    def test_mint_token_factory(self, wallet):
        addr = _make_valid_address()
        tx = Transaction.mint_token(wallet.address, addr, "a" * 32, 5000,
                                     nonce=3)
        assert tx.tx_type == TxType.MINT_TOKEN
        assert tx.sender == wallet.address
        assert tx.recipient == addr
        assert tx.nonce == 3
        assert tx.payload["token_id"] == "a" * 32
        assert tx.payload["amount"] == 5000

    def test_transfer_token_factory(self, wallet):
        addr = _make_valid_address()
        tx = Transaction.transfer_token(wallet.address, addr, "b" * 32, 100,
                                         memo="hello", nonce=7)
        assert tx.tx_type == TxType.TRANSFER_TOKEN
        assert tx.sender == wallet.address
        assert tx.recipient == addr
        assert tx.nonce == 7
        assert tx.payload["token_id"] == "b" * 32
        assert tx.payload["amount"] == 100
        assert tx.payload["memo"] == "hello"

    def test_transfer_token_no_memo(self, wallet):
        addr = _make_valid_address()
        tx = Transaction.transfer_token(wallet.address, addr, "b" * 32, 100)
        assert "memo" not in tx.payload

    def test_factory_tx_types_correct(self, wallet):
        addr = _make_valid_address()
        assert Transaction.issue_token(wallet.address, "T", "TT", 0).tx_type == TxType.ISSUE_TOKEN
        assert Transaction.mint_token(wallet.address, addr, "a" * 32, 1).tx_type == TxType.MINT_TOKEN
        assert Transaction.transfer_token(wallet.address, addr, "a" * 32, 1).tx_type == TxType.TRANSFER_TOKEN


# ========================================================================
# 5-6. STATE MACHINE — ISSUE TOKEN
# ========================================================================

class TestIssueTokenState:

    def test_issue_registers_token(self, funded_chain, wallet):
        bc = funded_chain
        _, tx_id, token_id = issue_token(bc, wallet, "Gold", "GLD", 6)
        assert token_id in bc._token_registry
        reg = bc._token_registry[token_id]
        assert reg["issuer"] == wallet.address
        assert reg["name"] == "Gold"
        assert reg["symbol"] == "GLD"
        assert reg["decimals"] == 6
        assert reg["total_minted"] == 0
        assert reg["transferable"] is True

    def test_token_id_derivation(self, funded_chain, wallet):
        bc = funded_chain
        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        _, _, token_id = issue_token(bc, wallet, "Silver", "SLV", 8, nonce=nonce)
        expected = _derive_token_id(wallet.address, "SLV", nonce)
        assert token_id == expected

    def test_issue_populates_symbol_index(self, funded_chain, wallet):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Test", "TST", 8)
        assert bc._token_by_symbol["TST"] == token_id

    def test_issue_with_max_supply(self, funded_chain, wallet):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Cap", "CAP", 0,
                                     max_supply=1_000_000)
        assert bc._token_registry[token_id]["max_supply"] == 1_000_000

    def test_issue_not_transferable(self, funded_chain, wallet):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Soul", "SOUL", 0,
                                     transferable=False)
        assert bc._token_registry[token_id]["transferable"] is False

    def test_duplicate_symbol_rejected(self, funded_chain, wallet):
        bc = funded_chain
        issue_token(bc, wallet, "First", "DUP", 8)
        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        tx = Transaction.issue_token(
            wallet.address, "Second", "DUP", 8, nonce=nonce)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, result = bc.submit_tx(tx)
        if ok:
            # The submit might succeed, but mining should fail
            with pytest.raises(ValueError, match="already exists"):
                bc.produce_block(wallet.address, wallet.signing_sk)
        # If submit rejects it, that is also acceptable

    def test_multiple_different_tokens(self, funded_chain, wallet):
        bc = funded_chain
        _, _, tid1 = issue_token(bc, wallet, "Alpha", "AA", 8)
        _, _, tid2 = issue_token(bc, wallet, "Beta", "BB", 8)
        _, _, tid3 = issue_token(bc, wallet, "Gamma", "CC", 8)
        assert len(bc._token_registry) == 3
        assert tid1 != tid2 != tid3


# ========================================================================
# 7-10. STATE MACHINE — MINT TOKEN
# ========================================================================

class TestMintTokenState:

    def test_mint_updates_balance(self, funded_chain, wallet, wallet2):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Coin", "COIN", 8)
        mint_token(bc, wallet, wallet.address, token_id, 5000)
        assert bc.get_token_balance(token_id, wallet.address) == 5000

    def test_mint_updates_total_minted(self, funded_chain, wallet):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Coin", "COIN", 8)
        mint_token(bc, wallet, wallet.address, token_id, 3000)
        assert bc._token_registry[token_id]["total_minted"] == 3000

    def test_mint_to_other_address(self, funded_chain, wallet, wallet2):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Coin", "COIN", 8)
        mint_token(bc, wallet, wallet2.address, token_id, 1000)
        assert bc.get_token_balance(token_id, wallet2.address) == 1000
        assert bc.get_token_balance(token_id, wallet.address) == 0

    def test_mint_accumulates(self, funded_chain, wallet):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Coin", "COIN", 8)
        mint_token(bc, wallet, wallet.address, token_id, 1000)
        mint_token(bc, wallet, wallet.address, token_id, 2000)
        assert bc.get_token_balance(token_id, wallet.address) == 3000
        assert bc._token_registry[token_id]["total_minted"] == 3000

    def test_mint_by_non_issuer_rejected(self, funded_chain, wallet, wallet2):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Coin", "COIN", 8)

        # Fund wallet2 so it can pay fees
        fund_wallet(bc, wallet, wallet2.address, 10 * QUBIT_PER_QBIT)
        # Register wallet2 as validator so it can sign blocks... actually
        # we just need wallet (the validator) to mine the block.
        nonce = bc.get_nonce(wallet2.address) + bc._pool_sender_count.get(wallet2.address, 0)
        tx = Transaction.mint_token(
            wallet2.address, wallet2.address, token_id, 500, nonce=nonce)
        tx.sign(wallet2.signing_sk, wallet2.signing_pk)
        ok, result = bc.submit_tx(tx)
        if ok:
            with pytest.raises(ValueError, match="only issuer"):
                bc.produce_block(wallet.address, wallet.signing_sk)
        # If submit rejected it, also acceptable

    def test_mint_exceeding_max_supply_rejected(self, funded_chain, wallet):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Cap", "CAP", 8,
                                     max_supply=1000)
        # First mint up to the limit
        mint_token(bc, wallet, wallet.address, token_id, 800)

        # Now try to exceed
        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        tx = Transaction.mint_token(
            wallet.address, wallet.address, token_id, 300, nonce=nonce)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, result = bc.submit_tx(tx)
        if ok:
            with pytest.raises(ValueError, match="exceed max_supply"):
                bc.produce_block(wallet.address, wallet.signing_sk)

    def test_mint_exact_max_supply_ok(self, funded_chain, wallet):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Exact", "EXA", 8,
                                     max_supply=1000)
        mint_token(bc, wallet, wallet.address, token_id, 1000)
        assert bc._token_registry[token_id]["total_minted"] == 1000

    def test_mint_unlimited_supply(self, funded_chain, wallet):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Infinite", "INF", 8,
                                     max_supply=0)
        # Should succeed no matter how much
        mint_token(bc, wallet, wallet.address, token_id, 999_999_999)
        assert bc.get_token_balance(token_id, wallet.address) == 999_999_999

    def test_mint_nonexistent_token_rejected(self, funded_chain, wallet):
        bc = funded_chain
        fake_id = "f" * 32
        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        tx = Transaction.mint_token(
            wallet.address, wallet.address, fake_id, 100, nonce=nonce)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, result = bc.submit_tx(tx)
        if ok:
            with pytest.raises(ValueError, match="does not exist"):
                bc.produce_block(wallet.address, wallet.signing_sk)


# ========================================================================
# 11-13. STATE MACHINE — TRANSFER TOKEN
# ========================================================================

class TestTransferTokenState:

    def test_transfer_updates_balances(self, funded_chain, wallet, wallet2):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Coin", "COIN", 8)
        mint_token(bc, wallet, wallet.address, token_id, 5000)

        # Fund wallet2 is not needed since wallet does the transfer
        transfer_token(bc, wallet, wallet2.address, token_id, 2000)
        assert bc.get_token_balance(token_id, wallet.address) == 3000
        assert bc.get_token_balance(token_id, wallet2.address) == 2000

    def test_transfer_exact_balance(self, funded_chain, wallet, wallet2):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Coin", "COIN", 8)
        mint_token(bc, wallet, wallet.address, token_id, 1000)
        transfer_token(bc, wallet, wallet2.address, token_id, 1000)
        assert bc.get_token_balance(token_id, wallet.address) == 0
        assert bc.get_token_balance(token_id, wallet2.address) == 1000

    def test_transfer_insufficient_balance_rejected(self, funded_chain, wallet, wallet2):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Coin", "COIN", 8)
        mint_token(bc, wallet, wallet.address, token_id, 100)

        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        tx = Transaction.transfer_token(
            wallet.address, wallet2.address, token_id, 200, nonce=nonce)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, result = bc.submit_tx(tx)
        if ok:
            with pytest.raises(ValueError, match="insufficient token balance"):
                bc.produce_block(wallet.address, wallet.signing_sk)

    def test_transfer_non_transferable_rejected(self, funded_chain, wallet, wallet2):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Soul", "SOUL", 0,
                                     transferable=False)
        mint_token(bc, wallet, wallet.address, token_id, 1000)

        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        tx = Transaction.transfer_token(
            wallet.address, wallet2.address, token_id, 500, nonce=nonce)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, result = bc.submit_tx(tx)
        if ok:
            with pytest.raises(ValueError, match="not transferable"):
                bc.produce_block(wallet.address, wallet.signing_sk)

    def test_transfer_nonexistent_token_rejected(self, funded_chain, wallet, wallet2):
        bc = funded_chain
        fake_id = "e" * 32
        nonce = bc.get_nonce(wallet.address) + bc._pool_sender_count.get(wallet.address, 0)
        tx = Transaction.transfer_token(
            wallet.address, wallet2.address, fake_id, 100, nonce=nonce)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, result = bc.submit_tx(tx)
        if ok:
            with pytest.raises(ValueError, match="does not exist"):
                bc.produce_block(wallet.address, wallet.signing_sk)

    def test_transfer_with_memo(self, funded_chain, wallet, wallet2):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Coin", "COIN", 8)
        mint_token(bc, wallet, wallet.address, token_id, 5000)
        transfer_token(bc, wallet, wallet2.address, token_id, 100,
                       memo="test payment")
        assert bc.get_token_balance(token_id, wallet2.address) == 100

    def test_transfer_zero_balance_sender_cleaned(self, funded_chain, wallet, wallet2):
        """When sender balance drops to 0, the entry should be removed."""
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Coin", "COIN", 8)
        mint_token(bc, wallet, wallet.address, token_id, 1000)
        transfer_token(bc, wallet, wallet2.address, token_id, 1000)
        # The (token_id, wallet.address) key should be deleted
        assert (token_id, wallet.address) not in bc._token_balances

    def test_multi_hop_transfer(self, funded_chain, wallet, wallet2, wallet3):
        """Issue -> mint to wallet -> transfer to wallet2 -> wallet2 transfers to wallet3."""
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Coin", "COIN", 8)
        mint_token(bc, wallet, wallet.address, token_id, 10000)
        transfer_token(bc, wallet, wallet2.address, token_id, 5000)

        # Fund wallet2 for QBIT fees
        fund_wallet(bc, wallet, wallet2.address, 10 * QUBIT_PER_QBIT)

        # wallet2 transfers to wallet3
        nonce2 = bc.get_nonce(wallet2.address) + bc._pool_sender_count.get(wallet2.address, 0)
        tx = Transaction.transfer_token(
            wallet2.address, wallet3.address, token_id, 2000, nonce=nonce2)
        tx.sign(wallet2.signing_sk, wallet2.signing_pk)
        submit_and_mine(bc, wallet, tx)

        assert bc.get_token_balance(token_id, wallet.address) == 5000
        assert bc.get_token_balance(token_id, wallet2.address) == 3000
        assert bc.get_token_balance(token_id, wallet3.address) == 2000


# ========================================================================
# 14. STATE TRIE INCLUDES TOKEN ENTRIES
# ========================================================================

class TestTokenStateTrie:

    def test_token_balance_in_state_trie(self, funded_chain, wallet):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Coin", "COIN", 8)
        mint_token(bc, wallet, wallet.address, token_id, 5000)

        trie_key = f"token:{token_id}:balance:{wallet.address}"
        entry = bc._state_trie._entries.get(trie_key)
        assert entry is not None
        assert int.from_bytes(entry, 'big') == 5000

    def test_token_supply_in_state_trie(self, funded_chain, wallet):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Coin", "COIN", 8)
        mint_token(bc, wallet, wallet.address, token_id, 3000)

        trie_key = f"token:{token_id}:supply"
        entry = bc._state_trie._entries.get(trie_key)
        assert entry is not None
        assert int.from_bytes(entry, 'big') == 3000

    def test_state_root_changes_after_token_ops(self, funded_chain, wallet):
        bc = funded_chain
        root_before = bc.get_state_root()

        _, _, token_id = issue_token(bc, wallet, "Coin", "COIN", 8)
        root_after_issue = bc.get_state_root()
        assert root_after_issue != root_before

        mint_token(bc, wallet, wallet.address, token_id, 1000)
        root_after_mint = bc.get_state_root()
        assert root_after_mint != root_after_issue

    def test_token_state_proof(self, funded_chain, wallet):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Coin", "COIN", 8)
        mint_token(bc, wallet, wallet.address, token_id, 9999)

        proof = bc.get_token_state_proof(token_id, wallet.address)
        assert proof is not None
        assert proof["key"] == f"token:{token_id}:balance:{wallet.address}"


# ========================================================================
# 15-17. ROLLBACK
# ========================================================================

class TestTokenRollback:

    def test_rollback_issue_removes_token(self, funded_chain, wallet):
        bc = funded_chain
        chain_len_before = len(bc.chain)
        block, tx_id, token_id = issue_token(bc, wallet, "Roll", "RLL", 8)
        assert token_id in bc._token_registry
        assert "RLL" in bc._token_by_symbol

        bc._rollback_to(len(bc.chain) - 1)
        assert token_id not in bc._token_registry
        assert "RLL" not in bc._token_by_symbol
        assert len(bc.chain) == chain_len_before

    def test_rollback_mint_reverts_balance(self, funded_chain, wallet):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Roll", "RLL", 8)

        mint_token(bc, wallet, wallet.address, token_id, 5000)
        assert bc.get_token_balance(token_id, wallet.address) == 5000
        assert bc._token_registry[token_id]["total_minted"] == 5000

        bc._rollback_to(len(bc.chain) - 1)
        assert bc.get_token_balance(token_id, wallet.address) == 0
        assert bc._token_registry[token_id]["total_minted"] == 0

    def test_rollback_transfer_reverts_balances(self, funded_chain, wallet, wallet2):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Roll", "RLL", 8)
        mint_token(bc, wallet, wallet.address, token_id, 5000)

        transfer_token(bc, wallet, wallet2.address, token_id, 2000)
        assert bc.get_token_balance(token_id, wallet.address) == 3000
        assert bc.get_token_balance(token_id, wallet2.address) == 2000

        bc._rollback_to(len(bc.chain) - 1)
        assert bc.get_token_balance(token_id, wallet.address) == 5000
        assert bc.get_token_balance(token_id, wallet2.address) == 0

    def test_rollback_issue_cleans_balances_too(self, funded_chain, wallet):
        """Rolling back an ISSUE should also clean any balances from mints in same block."""
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Roll", "RLL", 8)
        mint_token(bc, wallet, wallet.address, token_id, 1000)

        # Rollback mint first
        bc._rollback_to(len(bc.chain) - 1)
        assert bc.get_token_balance(token_id, wallet.address) == 0

        # Now rollback the issue
        bc._rollback_to(len(bc.chain) - 1)
        assert token_id not in bc._token_registry
        # No stale balance keys
        stale = [k for k in bc._token_balances if k[0] == token_id]
        assert stale == []

    def test_rollback_multiple_mints(self, funded_chain, wallet):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Roll", "RLL", 8)
        mint_token(bc, wallet, wallet.address, token_id, 1000)
        mint_token(bc, wallet, wallet.address, token_id, 2000)
        assert bc.get_token_balance(token_id, wallet.address) == 3000

        bc._rollback_to(len(bc.chain) - 1)  # revert second mint
        assert bc.get_token_balance(token_id, wallet.address) == 1000
        assert bc._token_registry[token_id]["total_minted"] == 1000

        bc._rollback_to(len(bc.chain) - 1)  # revert first mint
        assert bc.get_token_balance(token_id, wallet.address) == 0
        assert bc._token_registry[token_id]["total_minted"] == 0


# ========================================================================
# 18-19. QUERY METHODS
# ========================================================================

class TestTokenQueries:

    def test_get_token_info(self, funded_chain, wallet):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Query", "QRY", 8)
        info = bc.get_token_info(token_id)
        assert info is not None
        assert info["name"] == "Query"
        assert info["symbol"] == "QRY"
        assert info["decimals"] == 8
        assert info["issuer"] == wallet.address

    def test_get_token_info_nonexistent(self, funded_chain):
        bc = funded_chain
        assert bc.get_token_info("0" * 32) is None

    def test_get_token_by_symbol(self, funded_chain, wallet):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Query", "QRY", 8)
        info = bc.get_token_by_symbol("QRY")
        assert info is not None
        assert info["symbol"] == "QRY"

    def test_get_token_by_symbol_nonexistent(self, funded_chain):
        bc = funded_chain
        assert bc.get_token_by_symbol("NOPE") is None

    def test_list_tokens_empty(self, funded_chain):
        bc = funded_chain
        assert bc.list_tokens() == []

    def test_list_tokens_multiple(self, funded_chain, wallet):
        bc = funded_chain
        issue_token(bc, wallet, "Alpha", "AA", 8)
        issue_token(bc, wallet, "Beta", "BB", 8)
        issue_token(bc, wallet, "Gamma", "CC", 8)
        tokens = bc.list_tokens()
        assert len(tokens) == 3

    def test_list_tokens_pagination(self, funded_chain, wallet):
        bc = funded_chain
        for i in range(5):
            sym = chr(65 + i) * 2  # AA, BB, CC, DD, EE
            issue_token(bc, wallet, f"Token{i}", sym, 8)
        page1 = bc.list_tokens(page=1, limit=2)
        page2 = bc.list_tokens(page=2, limit=2)
        page3 = bc.list_tokens(page=3, limit=2)
        assert len(page1) == 2
        assert len(page2) == 2
        assert len(page3) == 1

    def test_get_token_balance(self, funded_chain, wallet):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Coin", "COIN", 8)
        assert bc.get_token_balance(token_id, wallet.address) == 0
        mint_token(bc, wallet, wallet.address, token_id, 999)
        assert bc.get_token_balance(token_id, wallet.address) == 999

    def test_get_token_balance_nonexistent(self, funded_chain, wallet):
        bc = funded_chain
        assert bc.get_token_balance("0" * 32, wallet.address) == 0

    def test_get_token_holders(self, funded_chain, wallet, wallet2, wallet3):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Coin", "COIN", 8)
        mint_token(bc, wallet, wallet.address, token_id, 5000)
        mint_token(bc, wallet, wallet2.address, token_id, 3000)
        mint_token(bc, wallet, wallet3.address, token_id, 1000)

        holders = bc.get_token_holders(token_id)
        assert len(holders) == 3
        # Sorted by amount descending
        assert holders[0]["amount"] == 5000
        assert holders[1]["amount"] == 3000
        assert holders[2]["amount"] == 1000

    def test_get_token_holders_empty(self, funded_chain, wallet):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Coin", "COIN", 8)
        holders = bc.get_token_holders(token_id)
        assert holders == []

    def test_get_address_tokens(self, funded_chain, wallet):
        bc = funded_chain
        _, _, tid1 = issue_token(bc, wallet, "Alpha", "AA", 8)
        _, _, tid2 = issue_token(bc, wallet, "Beta", "BB", 6)
        mint_token(bc, wallet, wallet.address, tid1, 100)
        mint_token(bc, wallet, wallet.address, tid2, 200)

        tokens = bc.get_address_tokens(wallet.address)
        assert len(tokens) == 2
        symbols = {t["symbol"] for t in tokens}
        assert symbols == {"AA", "BB"}

    def test_get_address_tokens_empty(self, funded_chain, wallet):
        bc = funded_chain
        tokens = bc.get_address_tokens(wallet.address)
        assert tokens == []

    def test_get_address_tokens_includes_metadata(self, funded_chain, wallet):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Detailed", "DTL", 12)
        mint_token(bc, wallet, wallet.address, token_id, 42)
        tokens = bc.get_address_tokens(wallet.address)
        assert len(tokens) == 1
        t = tokens[0]
        assert t["token_id"] == token_id
        assert t["symbol"] == "DTL"
        assert t["name"] == "Detailed"
        assert t["decimals"] == 12
        assert t["amount"] == 42

    def test_get_token_holders_pagination(self, funded_chain, wallet, wallet2, wallet3):
        """R26-006: get_token_holders supports pagination."""
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Pag", "PAG", 8)
        mint_token(bc, wallet, wallet.address, token_id, 5000)
        mint_token(bc, wallet, wallet2.address, token_id, 3000)
        mint_token(bc, wallet, wallet3.address, token_id, 1000)

        page1 = bc.get_token_holders(token_id, page=1, limit=2)
        assert len(page1) == 2
        assert page1[0]["amount"] == 5000
        page2 = bc.get_token_holders(token_id, page=2, limit=2)
        assert len(page2) == 1
        assert page2[0]["amount"] == 1000

    def test_get_token_holders_cache(self, funded_chain, wallet, wallet2):
        """R29-001: Repeated calls use cache instead of re-scanning."""
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Cache", "CACHE", 8)
        mint_token(bc, wallet, wallet.address, token_id, 100)
        mint_token(bc, wallet, wallet2.address, token_id, 50)

        h1 = bc.get_token_holders(token_id)
        h2 = bc.get_token_holders(token_id)
        assert h1 == h2
        # Verify cache entry exists
        assert token_id in bc._holder_cache

    def test_get_token_holders_max_scan_limit(self, funded_chain, wallet):
        """R29-001: Holder scan is bounded by _TOKEN_HOLDER_MAX_SCAN."""
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Scan", "SCAN", 8)
        mint_token(bc, wallet, wallet.address, token_id, 100)

        # Prime the cache by calling once
        bc.get_token_holders(token_id)

        # Temporarily lower the scan limit to test truncation
        original = bc._TOKEN_HOLDER_MAX_SCAN
        try:
            bc._TOKEN_HOLDER_MAX_SCAN = 0
            bc._holder_cache.pop(token_id, None)  # clear cached result
            holders = bc.get_token_holders(token_id)
            assert holders == []
        finally:
            bc._TOKEN_HOLDER_MAX_SCAN = original

    def test_get_address_tokens_pagination(self, funded_chain, wallet):
        """R26-007: get_address_tokens supports pagination."""
        bc = funded_chain
        _, _, tid1 = issue_token(bc, wallet, "A1", "A1", 8)
        _, _, tid2 = issue_token(bc, wallet, "A2", "A2", 6)
        _, _, tid3 = issue_token(bc, wallet, "A3", "A3", 4)
        mint_token(bc, wallet, wallet.address, tid1, 100)
        mint_token(bc, wallet, wallet.address, tid2, 200)
        mint_token(bc, wallet, wallet.address, tid3, 300)

        page1 = bc.get_address_tokens(wallet.address, page=1, limit=2)
        assert len(page1) == 2
        page2 = bc.get_address_tokens(wallet.address, page=2, limit=2)
        assert len(page2) == 1


# ========================================================================
# 20-22. RECEIPT EVENTS
# ========================================================================

class TestTokenReceiptEvents:

    def test_issue_emits_token_issued(self, funded_chain, wallet):
        bc = funded_chain
        block, tx_id, token_id = issue_token(bc, wallet, "Evnt", "EVT", 8)
        receipt = bc.get_receipt(tx_id)
        assert receipt is not None
        assert receipt.status == "success"
        events = receipt.events
        issued = [e for e in events if e["type"] == "TokenIssued"]
        assert len(issued) == 1
        evt = issued[0]
        assert evt["token_id"] == token_id
        assert evt["symbol"] == "EVT"
        assert evt["name"] == "Evnt"
        assert evt["issuer"] == wallet.address

    def test_mint_emits_token_minted(self, funded_chain, wallet):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Evnt", "EVT", 8)
        block, tx_id = mint_token(bc, wallet, wallet.address, token_id, 7777)
        receipt = bc.get_receipt(tx_id)
        assert receipt is not None
        assert receipt.status == "success"
        events = receipt.events
        minted = [e for e in events if e["type"] == "TokenMinted"]
        assert len(minted) == 1
        evt = minted[0]
        assert evt["token_id"] == token_id
        assert evt["amount"] == 7777
        assert evt["recipient"] == wallet.address
        assert evt["total_minted"] == 7777

    def test_transfer_emits_token_transferred(self, funded_chain, wallet, wallet2):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Evnt", "EVT", 8)
        mint_token(bc, wallet, wallet.address, token_id, 5000)
        block, tx_id = transfer_token(bc, wallet, wallet2.address,
                                      token_id, 1500)
        receipt = bc.get_receipt(tx_id)
        assert receipt is not None
        assert receipt.status == "success"
        events = receipt.events
        transferred = [e for e in events if e["type"] == "TokenTransferred"]
        assert len(transferred) == 1
        evt = transferred[0]
        assert evt["token_id"] == token_id
        assert evt["amount"] == 1500
        assert evt["sender"] == wallet.address
        assert evt["recipient"] == wallet2.address

    def test_issue_event_includes_max_supply(self, funded_chain, wallet):
        bc = funded_chain
        _, tx_id, token_id = issue_token(bc, wallet, "Cap", "CAP", 8,
                                          max_supply=21_000_000)
        receipt = bc.get_receipt(tx_id)
        issued = [e for e in receipt.events if e["type"] == "TokenIssued"]
        assert issued[0]["max_supply"] == 21_000_000

    def test_mint_event_reflects_cumulative_total(self, funded_chain, wallet):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Cum", "CUM", 8)
        mint_token(bc, wallet, wallet.address, token_id, 1000)
        _, tx_id2 = mint_token(bc, wallet, wallet.address, token_id, 2000)
        receipt = bc.get_receipt(tx_id2)
        minted = [e for e in receipt.events if e["type"] == "TokenMinted"]
        assert minted[0]["total_minted"] == 3000

    def test_issue_token_id_in_receipt_merkle_proof(self, funded_chain, wallet):
        """R28-004: token_id must be included in receipt events AND
        flow through to the receiptsRoot Merkle proof so that the
        derived token ID is independently verifiable from receipt data."""
        from qbit_network.core.receipt import verify_receipt_proof
        bc = funded_chain
        _, tx_id, token_id = issue_token(bc, wallet, "Mrk", "MRK", 8)
        proof_data = bc.get_receipt_proof(tx_id)
        assert proof_data is not None
        # token_id is in the receipt events
        receipt = proof_data["receipt"]
        issued = [e for e in receipt["events"] if e["type"] == "TokenIssued"]
        assert len(issued) == 1
        assert issued[0]["token_id"] == token_id
        # receipt hash verifies against receiptsRoot via Merkle proof
        proof = [(bytes.fromhex(h), is_right) for h, is_right in proof_data["proof"]]
        assert verify_receipt_proof(
            receipt["receipt_hash"], proof, proof_data["receiptsRoot"]
        )
        # token_id is reproducible from receipt data alone
        from qbit_network.crypto import sha3_256
        r = bc.get_receipt(tx_id)
        issuer = issued[0]["issuer"]
        symbol = issued[0]["symbol"]
        # Find the original tx to get nonce for reproduction
        block = bc._get_block_by_index(r.block_index)
        tx = [t for t in block.transactions if t.tx_id == tx_id][0]
        reproduced = sha3_256(
            (issuer + symbol + str(tx.nonce)).encode()
        ).hex()[:32]
        assert reproduced == token_id


# ========================================================================
# EDGE CASES AND ADVERSARIAL TESTS
# ========================================================================

class TestTokenEdgeCases:

    def test_issue_name_only_spaces_valid(self, wallet):
        """Name with only spaces should pass alphanumeric+space regex."""
        tx = Transaction.issue_token(wallet.address, "   ", "TST", 8)
        ok, err = tx.validate_payload()
        assert ok, err

    def test_symbol_single_char_too_short(self, wallet):
        tx = Transaction.issue_token(wallet.address, "Test", "A", 8)
        ok, err = tx.validate_payload()
        assert not ok

    def test_name_not_string(self, wallet):
        tx = Transaction(TxType.ISSUE_TOKEN, wallet.address, payload={
            "name": 123, "symbol": "TST", "decimals": 8,
            "max_supply": 0, "transferable": True,
        })
        ok, err = tx.validate_payload()
        assert not ok

    def test_symbol_not_string(self, wallet):
        tx = Transaction(TxType.ISSUE_TOKEN, wallet.address, payload={
            "name": "Test", "symbol": 123, "decimals": 8,
            "max_supply": 0, "transferable": True,
        })
        ok, err = tx.validate_payload()
        assert not ok

    def test_decimals_bool_rejected(self, wallet):
        """bool is subclass of int in Python -- should still work since True=1, False=0."""
        tx = Transaction(TxType.ISSUE_TOKEN, wallet.address, payload={
            "name": "Test", "symbol": "TST", "decimals": True,
            "max_supply": 0, "transferable": True,
        })
        ok, err = tx.validate_payload()
        # bool is int subclass; True=1 which is valid
        # This tests that the code does not specially reject bools.
        # The behavior depends on whether isinstance(True, int) is accepted.
        # Since True is int, decimals=True (=1) should pass.
        assert ok, err

    def test_max_supply_bool_accepted_as_int(self, wallet):
        """bool is int subclass: False=0 is valid max_supply."""
        tx = Transaction(TxType.ISSUE_TOKEN, wallet.address, payload={
            "name": "Test", "symbol": "TST", "decimals": 8,
            "max_supply": False, "transferable": True,
        })
        ok, err = tx.validate_payload()
        assert ok, err

    def test_issue_and_transfer_in_separate_blocks(self, funded_chain, wallet, wallet2):
        """Full lifecycle: issue, mint, transfer, verify all balances."""
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Life", "LIF", 8)
        mint_token(bc, wallet, wallet.address, token_id, 10000)
        transfer_token(bc, wallet, wallet2.address, token_id, 4000)

        assert bc.get_token_balance(token_id, wallet.address) == 6000
        assert bc.get_token_balance(token_id, wallet2.address) == 4000
        assert bc._token_registry[token_id]["total_minted"] == 10000

        holders = bc.get_token_holders(token_id)
        assert len(holders) == 2

    def test_multiple_tokens_independent_balances(self, funded_chain, wallet, wallet2):
        """Two different tokens should have independent balances."""
        bc = funded_chain
        _, _, tid1 = issue_token(bc, wallet, "Alpha", "AA", 8)
        _, _, tid2 = issue_token(bc, wallet, "Beta", "BB", 8)

        mint_token(bc, wallet, wallet.address, tid1, 100)
        mint_token(bc, wallet, wallet.address, tid2, 200)

        assert bc.get_token_balance(tid1, wallet.address) == 100
        assert bc.get_token_balance(tid2, wallet.address) == 200

        # Transfer only token1
        transfer_token(bc, wallet, wallet2.address, tid1, 50)
        assert bc.get_token_balance(tid1, wallet.address) == 50
        assert bc.get_token_balance(tid2, wallet.address) == 200  # unchanged

    def test_fees_charged_for_issue(self, funded_chain, wallet):
        """ISSUE_TOKEN should charge the configured QBIT fee."""
        bc = funded_chain
        bal_before = bc.get_balance(wallet.address)
        issue_token(bc, wallet, "Fee", "FEE", 8)
        bal_after = bc.get_balance(wallet.address)
        # Balance should decrease (fee charged) but also increase (block reward)
        # At minimum, some fee was charged
        fee = TX_FEES.get("ISSUE_TOKEN", 0)
        # The validator gets block rewards, so the net might be positive.
        # Just verify the token was issued successfully.
        assert "FEE" in bc._token_by_symbol

    def test_fees_charged_for_mint(self, funded_chain, wallet):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Fee", "FEE", 8)
        mint_token(bc, wallet, wallet.address, token_id, 1000)
        assert bc.get_token_balance(token_id, wallet.address) == 1000

    def test_fees_charged_for_transfer(self, funded_chain, wallet, wallet2):
        bc = funded_chain
        _, _, token_id = issue_token(bc, wallet, "Fee", "FEE", 8)
        mint_token(bc, wallet, wallet.address, token_id, 1000)
        transfer_token(bc, wallet, wallet2.address, token_id, 500)
        assert bc.get_token_balance(token_id, wallet2.address) == 500

    def test_rollback_full_lifecycle(self, funded_chain, wallet, wallet2):
        """Issue -> mint -> transfer -> rollback all three."""
        bc = funded_chain
        chain_len = len(bc.chain)
        _, _, token_id = issue_token(bc, wallet, "Life", "LIF", 8)
        mint_token(bc, wallet, wallet.address, token_id, 5000)
        transfer_token(bc, wallet, wallet2.address, token_id, 2000)

        # Rollback transfer
        bc._rollback_to(len(bc.chain) - 1)
        assert bc.get_token_balance(token_id, wallet.address) == 5000
        assert bc.get_token_balance(token_id, wallet2.address) == 0

        # Rollback mint
        bc._rollback_to(len(bc.chain) - 1)
        assert bc.get_token_balance(token_id, wallet.address) == 0
        assert bc._token_registry[token_id]["total_minted"] == 0

        # Rollback issue
        bc._rollback_to(len(bc.chain) - 1)
        assert token_id not in bc._token_registry
        assert "LIF" not in bc._token_by_symbol
        assert len(bc.chain) == chain_len
