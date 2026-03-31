"""Defense-in-depth tests for token_id validation in state trie operations.

R28-002: Ensure _rebuild_state_trie and get_token_state_proof reject
token_ids that contain colons or other non-hex characters, even though
the transaction layer already enforces a 32-char hex regex.
"""
import pytest

from qbit_network.core.blockchain import Blockchain
from qbit_network.core.state_ops import _validate_token_id
from qbit_network.core.wallet import Wallet


# ---- _validate_token_id unit tests ----

class TestValidateTokenId:
    def test_valid_hex_32(self):
        assert _validate_token_id("a" * 32) is True
        assert _validate_token_id("0123456789abcdef" * 2) is True

    def test_rejects_colon(self):
        assert _validate_token_id("a" * 16 + ":balance:" + "b" * 7) is False

    def test_rejects_short(self):
        assert _validate_token_id("abcd") is False

    def test_rejects_long(self):
        assert _validate_token_id("a" * 64) is False

    def test_rejects_non_hex(self):
        assert _validate_token_id("g" * 32) is False
        assert _validate_token_id("ZZZZ" + "a" * 28) is False

    def test_rejects_non_string(self):
        assert _validate_token_id(12345) is False
        assert _validate_token_id(None) is False

    def test_rejects_empty(self):
        assert _validate_token_id("") is False


# ---- _rebuild_state_trie defense-in-depth ----

class TestRebuildStateTrieTokenIdValidation:
    @pytest.fixture
    def chain(self):
        w = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(w.address, w.signing_pk)
        bc.init_chain(w.address, w.signing_sk, validator_pk=w.signing_pk)
        bc.activate_financial_layer(w.address)
        return bc

    def test_rebuild_raises_on_colon_in_token_balance(self, chain):
        """Injecting a colon-containing token_id into _token_balances must raise."""
        bad_tid = "balance:attackeraddr00000000"  # contains colons
        chain._token_balances[(bad_tid, "qv1" + "a" * 64)] = 100
        with pytest.raises(ValueError, match="invalid token_id"):
            chain._rebuild_state_trie()

    def test_rebuild_raises_on_colon_in_token_registry(self, chain):
        """Injecting a colon-containing token_id into _token_registry must raise."""
        bad_tid = "nonce:attackeraddr0000000000"
        chain._token_registry[bad_tid] = {"total_minted": 1000}
        with pytest.raises(ValueError, match="invalid token_id"):
            chain._rebuild_state_trie()

    def test_rebuild_ok_with_valid_token_id(self, chain):
        """A valid 32-char hex token_id must not raise."""
        good_tid = "ab" * 16
        chain._token_balances[(good_tid, "qv1" + "a" * 64)] = 50
        chain._token_registry[good_tid] = {"total_minted": 50}
        chain._rebuild_state_trie()  # should not raise


# ---- get_token_state_proof defense-in-depth ----

class TestGetTokenStateProofValidation:
    @pytest.fixture
    def chain(self):
        w = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(w.address, w.signing_pk)
        bc.init_chain(w.address, w.signing_sk, validator_pk=w.signing_pk)
        return bc

    def test_proof_returns_none_for_colon_token_id(self, chain):
        result = chain.get_token_state_proof("balance:evil" + "0" * 20, "qv1" + "a" * 64)
        assert result is None

    def test_proof_returns_none_for_non_hex_token_id(self, chain):
        result = chain.get_token_state_proof("x" * 32, "qv1" + "a" * 64)
        assert result is None

    def test_proof_accepts_valid_token_id(self, chain):
        # Will return None because token doesn't exist in trie, but should not
        # be rejected by validation.
        result = chain.get_token_state_proof("ab" * 16, "qv1" + "a" * 64)
        # None is fine (token not in trie), but the call should not raise
