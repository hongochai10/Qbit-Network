"""Dedicated tests for rollback and fork/reorg scenarios.

Covers: single/multi-block rollback, TX-type-specific state reversal,
fee reversal (legacy + dynamic), burned counter, fork resolution,
edge cases (genesis, empty blocks), nonce recomputation, and index cleanup.
"""
import pytest
from qbit_network.core.wallet import Wallet
from qbit_network.core.blockchain import Blockchain
from qbit_network.core.transaction import Transaction, TxType
from qbit_network.config import (
    TX_FEES, GENESIS_BALANCE_QBIT, QUBIT_PER_QBIT, INITIAL_BLOCK_REWARD,
)
from qbit_network.crypto import sha3_256
from tests.conftest import submit_and_mine


GENESIS_BAL = GENESIS_BALANCE_QBIT * QUBIT_PER_QBIT
BLOCK_REWARD = INITIAL_BLOCK_REWARD  # 5 QBIT for block index 1+


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_chain():
    """Return (blockchain, validator_wallet) with financial layer active."""
    w = Wallet.generate()
    bc = Blockchain()
    bc.consensus.add_validator(w.address, w.signing_pk)
    bc.init_chain(w.address, w.signing_sk, validator_pk=w.signing_pk)
    bc.activate_financial_layer(w.address)
    return bc, w


_FILLER_WALLET = None

def _get_filler():
    global _FILLER_WALLET
    if _FILLER_WALLET is None:
        _FILLER_WALLET = Wallet.generate()
    return _FILLER_WALLET


def _mine_block(bc, w):
    """Mine a block with a small notarize tx (legacy mode needs >=1 tx)."""
    doc = sha3_256(f"filler-{bc.height}-{id(bc)}".encode()).hex()
    tx = Transaction.notarize(w.address, doc, nonce=_next_nonce(bc, w.address))
    tx.sign(w.signing_sk, w.signing_pk)
    ok, msg = bc.submit_tx(tx)
    assert ok, f"submit failed: {msg}"
    block = bc.produce_block(w.address, w.signing_sk)
    assert block is not None
    return block


def _mine_blocks(bc, w, n=1):
    """Mine n blocks."""
    for _ in range(n):
        _mine_block(bc, w)


def _next_nonce(bc, addr):
    return bc.get_nonce(addr)


def _derive_token_id(sender, symbol, nonce):
    return sha3_256((sender + symbol + str(nonce)).encode()).hex()[:32]


# ===========================================================================
# 1. Single block rollback -- basic state reversal
# ===========================================================================

class TestSingleBlockRollback:

    def test_rollback_block_restores_height(self):
        bc, w = _setup_chain()
        _mine_block(bc, w)
        assert bc.height == 1
        bc._rollback_to(1)
        assert bc.height == 0

    def test_rollback_preserves_genesis(self):
        bc, w = _setup_chain()
        _mine_block(bc, w)
        bc._rollback_to(1)
        assert bc.height == 0
        genesis = bc._get_block_by_index(0)
        assert genesis is not None

    def test_rollback_restores_balance_after_transfer(self):
        bc, w = _setup_chain()
        bob = Wallet.generate()
        bal_before = bc.get_balance(w.address)
        nonce = _next_nonce(bc, w.address)
        tx = Transaction.transfer(w.address, bob.address, 1_000_000_000, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)
        assert bc.get_balance(bob.address) > 0
        bc._rollback_to(1)
        assert bc.get_balance(bob.address) == 0
        # Sender balance restored (minus no fees since rolled back)
        assert bc.get_balance(w.address) == bal_before

    def test_rollback_restores_nonce(self):
        bc, w = _setup_chain()
        bob = Wallet.generate()
        nonce_before = _next_nonce(bc, w.address)
        tx = Transaction.transfer(w.address, bob.address, 100, nonce=nonce_before)
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)
        assert _next_nonce(bc, w.address) == nonce_before + 1
        bc._rollback_to(1)
        assert _next_nonce(bc, w.address) == nonce_before

    def test_rollback_removes_tx_from_index(self):
        bc, w = _setup_chain()
        bob = Wallet.generate()
        tx = Transaction.transfer(w.address, bob.address, 100, nonce=_next_nonce(bc, w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        _, tx_id = submit_and_mine(bc, w, tx)
        assert bc.get_tx(tx_id) is not None
        bc._rollback_to(1)
        assert bc.get_tx(tx_id) is None

    def test_rollback_removes_block_hash_index(self):
        bc, w = _setup_chain()
        _mine_block(bc, w)
        blk = bc._get_block_by_index(1)
        bh = blk.block_hash
        assert bc._block_by_hash.get(bh) is not None
        bc._rollback_to(1)
        assert bc._block_by_hash.get(bh) is None

    def test_rollback_reverses_block_reward(self):
        bc, w = _setup_chain()
        bal_before = bc.get_balance(w.address)
        minted_before = bc._total_minted
        _mine_block(bc, w)
        # Balance = before + reward - transfer(1) - fee + transfer_received(1, self-transfer)
        assert bc._total_minted > minted_before
        bc._rollback_to(1)
        assert bc.get_balance(w.address) == bal_before
        assert bc._total_minted == minted_before


# ===========================================================================
# 2. Multi-block rollback
# ===========================================================================

class TestMultiBlockRollback:

    def test_rollback_three_blocks(self):
        bc, w = _setup_chain()
        _mine_blocks(bc, w, 3)
        assert bc.height == 3
        bc._rollback_to(1)
        assert bc.height == 0

    def test_rollback_three_blocks_restores_balance(self):
        bc, w = _setup_chain()
        bob = Wallet.generate()
        bal_before = bc.get_balance(w.address)
        for i in range(3):
            tx = Transaction.transfer(w.address, bob.address, 100, nonce=_next_nonce(bc, w.address))
            tx.sign(w.signing_sk, w.signing_pk)
            submit_and_mine(bc, w, tx)
        assert bc.get_balance(bob.address) > 0
        bc._rollback_to(1)
        assert bc.get_balance(bob.address) == 0
        assert bc.get_balance(w.address) == bal_before

    def test_partial_rollback_keeps_earlier_blocks(self):
        bc, w = _setup_chain()
        bob = Wallet.generate()
        # Block 1: transfer
        tx = Transaction.transfer(w.address, bob.address, 500, nonce=_next_nonce(bc, w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        _, tx_id1 = submit_and_mine(bc, w, tx)
        bob_bal_after_1 = bc.get_balance(bob.address)
        # Block 2: another transfer
        tx2 = Transaction.transfer(w.address, bob.address, 300, nonce=_next_nonce(bc, w.address))
        tx2.sign(w.signing_sk, w.signing_pk)
        _, tx_id2 = submit_and_mine(bc, w, tx2)
        assert bc.height == 2
        # Rollback only block 2
        bc._rollback_to(2)
        assert bc.height == 1
        assert bc.get_tx(tx_id1) is not None
        assert bc.get_tx(tx_id2) is None
        assert bc.get_balance(bob.address) == bob_bal_after_1

    def test_rollback_returns_displaced_txs(self):
        bc, w = _setup_chain()
        bob = Wallet.generate()
        tx = Transaction.transfer(w.address, bob.address, 100, nonce=_next_nonce(bc, w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        _, tx_id = submit_and_mine(bc, w, tx)
        displaced = bc._rollback_to(1)
        assert any(t.tx_id == tx_id for t in displaced)

    def test_rollback_multiple_blocks_displaced_all(self):
        bc, w = _setup_chain()
        bob = Wallet.generate()
        tx_ids = []
        for _ in range(3):
            tx = Transaction.transfer(w.address, bob.address, 100, nonce=_next_nonce(bc, w.address))
            tx.sign(w.signing_sk, w.signing_pk)
            _, tid = submit_and_mine(bc, w, tx)
            tx_ids.append(tid)
        displaced = bc._rollback_to(1)
        displaced_ids = {t.tx_id for t in displaced}
        for tid in tx_ids:
            assert tid in displaced_ids


# ===========================================================================
# 3. TX-type-specific rollback (>= 5 types)
# ===========================================================================

class TestTxTypeRollback:

    def test_rollback_transfer(self):
        bc, w = _setup_chain()
        bob = Wallet.generate()
        bal_w = bc.get_balance(w.address)
        tx = Transaction.transfer(w.address, bob.address, 2_000_000_000, nonce=_next_nonce(bc, w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)
        bc._rollback_to(1)
        assert bc.get_balance(bob.address) == 0
        assert bc.get_balance(w.address) == bal_w

    def test_rollback_stake(self):
        bc, w = _setup_chain()
        bal_before = bc.get_balance(w.address)
        stake_before = bc._total_stake.get(w.address, 0)
        tx = Transaction.stake(w.address, w.address, 100, nonce=_next_nonce(bc, w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)
        assert bc._total_stake.get(w.address, 0) == stake_before + 100
        bc._rollback_to(1)
        assert bc._total_stake.get(w.address, 0) == stake_before
        assert bc.get_balance(w.address) == bal_before

    def test_rollback_delegate(self):
        bc, w = _setup_chain()
        bob = Wallet.generate()
        # Fund bob
        tx = Transaction.transfer(w.address, bob.address, 5_000_000_000, nonce=_next_nonce(bc, w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)
        bob_bal = bc.get_balance(bob.address)
        stake_before = bc._total_stake.get(w.address, 0)
        # Bob delegates to validator w
        tx2 = Transaction.delegate(bob.address, w.address, 50, nonce=_next_nonce(bc, bob.address))
        tx2.sign(bob.signing_sk, bob.signing_pk)
        submit_and_mine(bc, w, tx2)
        assert bc._total_stake.get(w.address, 0) == stake_before + 50
        bc._rollback_to(2)
        assert bc._total_stake.get(w.address, 0) == stake_before
        assert bc.get_balance(bob.address) == bob_bal

    def test_rollback_notarize(self):
        bc, w = _setup_chain()
        doc_hash = sha3_256(b"test-doc").hex()
        tx = Transaction.notarize(w.address, doc_hash, nonce=_next_nonce(bc, w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        _, tx_id = submit_and_mine(bc, w, tx)
        assert bc._notarizations.get(doc_hash) == tx_id
        bc._rollback_to(1)
        assert bc._notarizations.get(doc_hash) is None

    def test_rollback_register_key(self):
        bc, w = _setup_chain()
        tx = Transaction.register_key(w.address, w.encryption_pk, nonce=_next_nonce(bc, w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)
        assert bc._key_registry.get(w.address) is not None
        bc._rollback_to(1)
        assert bc._key_registry.get(w.address) is None

    def test_rollback_register_validator(self):
        bc, w = _setup_chain()
        new_v = Wallet.generate()
        # Fund new validator
        tx = Transaction.transfer(w.address, new_v.address, 5_000_000_000, nonce=_next_nonce(bc, w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)
        # Register
        tx2 = Transaction.register_validator(
            new_v.address, new_v.signing_pk, new_v.address, nonce=_next_nonce(bc, new_v.address))
        tx2.sign(new_v.signing_sk, new_v.signing_pk)
        submit_and_mine(bc, w, tx2)
        assert new_v.address in bc._validator_registry
        bc._rollback_to(2)
        assert new_v.address not in bc._validator_registry

    def test_rollback_unstake(self):
        bc, w = _setup_chain()
        # Stake first
        tx = Transaction.stake(w.address, w.address, 200, nonce=_next_nonce(bc, w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)
        stake_after = bc._total_stake.get(w.address, 0)
        unbonding_before = len(bc._unbonding)
        # Unstake
        tx2 = Transaction.unstake(w.address, w.address, 100, nonce=_next_nonce(bc, w.address))
        tx2.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx2)
        assert bc._total_stake.get(w.address, 0) == stake_after - 100
        assert len(bc._unbonding) == unbonding_before + 1
        # Rollback unstake
        bc._rollback_to(2)
        assert bc._total_stake.get(w.address, 0) == stake_after
        assert len(bc._unbonding) == unbonding_before


# ===========================================================================
# 4. Token TX rollback
# ===========================================================================

class TestTokenRollback:

    def test_rollback_issue_token(self):
        bc, w = _setup_chain()
        nonce = _next_nonce(bc, w.address)
        tx = Transaction.issue_token(w.address, "Gold", "GLD", 6, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)
        token_id = _derive_token_id(w.address, "GLD", nonce)
        assert token_id in bc._token_registry
        bc._rollback_to(1)
        assert token_id not in bc._token_registry

    def test_rollback_mint_token(self):
        bc, w = _setup_chain()
        bob = Wallet.generate()
        nonce = _next_nonce(bc, w.address)
        tx = Transaction.issue_token(w.address, "Gold", "GLD", 6, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)
        token_id = _derive_token_id(w.address, "GLD", nonce)
        # Mint
        tx2 = Transaction.mint_token(w.address, bob.address, token_id, 5000, nonce=_next_nonce(bc, w.address))
        tx2.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx2)
        assert bc.get_token_balance(token_id, bob.address) == 5000
        bc._rollback_to(2)
        assert bc.get_token_balance(token_id, bob.address) == 0

    def test_rollback_transfer_token(self):
        bc, w = _setup_chain()
        bob = Wallet.generate()
        nonce = _next_nonce(bc, w.address)
        tx = Transaction.issue_token(w.address, "Gold", "GLD", 6, nonce=nonce)
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)
        token_id = _derive_token_id(w.address, "GLD", nonce)
        # Mint to self
        tx2 = Transaction.mint_token(w.address, w.address, token_id, 1000, nonce=_next_nonce(bc, w.address))
        tx2.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx2)
        w_bal = bc.get_token_balance(token_id, w.address)
        # Transfer
        tx3 = Transaction.transfer_token(w.address, bob.address, token_id, 400, nonce=_next_nonce(bc, w.address))
        tx3.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx3)
        assert bc.get_token_balance(token_id, bob.address) == 400
        assert bc.get_token_balance(token_id, w.address) == w_bal - 400
        bc._rollback_to(3)
        assert bc.get_token_balance(token_id, bob.address) == 0
        assert bc.get_token_balance(token_id, w.address) == w_bal


# ===========================================================================
# 5. Legacy fee reversal
# ===========================================================================

class TestLegacyFeeReversal:

    def test_legacy_fee_reversed_on_rollback(self):
        bc, w = _setup_chain()
        bob = Wallet.generate()
        bal_before = bc.get_balance(w.address)
        burned_before = bc._total_burned
        tx = Transaction.transfer(w.address, bob.address, 100, nonce=_next_nonce(bc, w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)
        fee = TX_FEES.get("TRANSFER", 0)
        if fee > 0:
            assert bc._total_burned > burned_before
        bc._rollback_to(1)
        assert bc._total_burned == burned_before
        assert bc.get_balance(w.address) == bal_before

    def test_legacy_fee_burn_counter_reversed(self):
        bc, w = _setup_chain()
        bob = Wallet.generate()
        burned_before = bc._total_burned
        # Multiple txs to accumulate burn
        for _ in range(3):
            tx = Transaction.transfer(w.address, bob.address, 100, nonce=_next_nonce(bc, w.address))
            tx.sign(w.signing_sk, w.signing_pk)
            submit_and_mine(bc, w, tx)
        assert bc._total_burned > burned_before
        bc._rollback_to(1)
        assert bc._total_burned == burned_before

    def test_notarize_fee_reversed(self):
        bc, w = _setup_chain()
        bal_before = bc.get_balance(w.address)
        burned_before = bc._total_burned
        tx = Transaction.notarize(w.address, sha3_256(b"doc").hex(), nonce=_next_nonce(bc, w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)
        bc._rollback_to(1)
        assert bc._total_burned == burned_before
        assert bc.get_balance(w.address) == bal_before


# ===========================================================================
# 6. Dynamic (EIP-1559) fee reversal
# ===========================================================================

class TestDynamicFeeReversal:

    @pytest.fixture(autouse=True)
    def _activate_dynamic_fees(self, monkeypatch):
        monkeypatch.setattr("qbit_network.config.DYNAMIC_FEE_ACTIVATION_HEIGHT", 0)
        monkeypatch.setattr("qbit_network.core.blockchain.DYNAMIC_FEE_ACTIVATION_HEIGHT", 0)
        monkeypatch.setattr("qbit_network.core.consensus.DYNAMIC_FEE_ACTIVATION_HEIGHT", 0)

    def _setup_funded_pair(self):
        """Setup chain + fund bob directly (avoids self-tx 25% cap)."""
        bc, w = _setup_chain()
        bob = Wallet.generate()
        bc._credit(bob.address, 100_000_000_000)  # 100 QBIT
        return bc, w, bob

    def test_dynamic_fee_reversed_transfer(self):
        bc, w, bob = self._setup_funded_pair()
        val_bal = bc.get_balance(w.address)
        bob_bal = bc.get_balance(bob.address)
        # Bob sends to w (bob is not validator, avoids self-tx cap)
        tx = Transaction.transfer(
            bob.address, w.address, 100, nonce=_next_nonce(bc, bob.address),
            max_fee_per_weight=100, max_priority_fee=10)
        tx.sign(bob.signing_sk, bob.signing_pk)
        submit_and_mine(bc, w, tx)
        assert bc.height == 1
        bc._rollback_to(1)  # back to genesis
        assert bc.get_balance(w.address) == val_bal
        assert bc.get_balance(bob.address) == bob_bal

    def test_dynamic_fee_validator_credit_reversed(self):
        bc, w, bob = self._setup_funded_pair()
        val_bal = bc.get_balance(w.address)
        # Bob sends to trigger fee credited to validator
        tx = Transaction.transfer(
            bob.address, w.address, 100, nonce=_next_nonce(bc, bob.address),
            max_fee_per_weight=100, max_priority_fee=10)
        tx.sign(bob.signing_sk, bob.signing_pk)
        submit_and_mine(bc, w, tx)
        # Validator got block reward + fee
        assert bc.get_balance(w.address) > val_bal
        bc._rollback_to(1)  # back to genesis
        assert bc.get_balance(w.address) == val_bal


# ===========================================================================
# 7. Fork resolution simulation (rollback + re-apply)
# ===========================================================================

class TestForkResolution:

    def test_rollback_and_reapply_different_tx(self):
        bc, w = _setup_chain()
        bob = Wallet.generate()
        carol = Wallet.generate()
        bal_before = bc.get_balance(w.address)
        # Mine block with transfer to bob
        tx1 = Transaction.transfer(w.address, bob.address, 1000, nonce=_next_nonce(bc, w.address))
        tx1.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx1)
        # Rollback
        bc._rollback_to(1)
        assert bc.get_balance(bob.address) == 0
        assert bc.get_balance(w.address) == bal_before
        # Re-apply different tx (simulate accepting fork's block)
        tx2 = Transaction.transfer(w.address, carol.address, 2000, nonce=_next_nonce(bc, w.address))
        tx2.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx2)
        assert bc.get_balance(carol.address) > 0
        assert bc.get_balance(bob.address) == 0

    def test_rollback_reapply_preserves_chain_integrity(self):
        bc, w = _setup_chain()
        bob = Wallet.generate()
        # Build 3 blocks
        for i in range(3):
            tx = Transaction.transfer(w.address, bob.address, 100, nonce=_next_nonce(bc, w.address))
            tx.sign(w.signing_sk, w.signing_pk)
            submit_and_mine(bc, w, tx)
        assert bc.height == 3
        # Rollback to block 1
        bc._rollback_to(2)
        assert bc.height == 1
        # Re-apply 2 new blocks
        for i in range(2):
            tx = Transaction.transfer(w.address, bob.address, 50, nonce=_next_nonce(bc, w.address))
            tx.sign(w.signing_sk, w.signing_pk)
            submit_and_mine(bc, w, tx)
        assert bc.height == 3
        # Chain should be valid
        for i in range(1, bc.height + 1):
            blk = bc._get_block_by_index(i)
            assert blk is not None
            assert blk.index == i


# ===========================================================================
# 8. Edge cases
# ===========================================================================

class TestEdgeCases:

    def test_rollback_to_genesis(self):
        bc, w = _setup_chain()
        _mine_blocks(bc, w, 5)
        assert bc.height == 5
        bc._rollback_to(1)
        assert bc.height == 0
        assert bc._get_block_by_index(0) is not None

    def test_rollback_blocks_restores_balance(self):
        bc, w = _setup_chain()
        bal_before = bc.get_balance(w.address)
        _mine_blocks(bc, w, 3)
        bc._rollback_to(1)
        assert bc.get_balance(w.address) == bal_before

    def test_rollback_noop_when_already_at_target(self):
        bc, w = _setup_chain()
        _mine_block(bc, w)
        assert bc.height == 1
        displaced = bc._rollback_to(2)
        assert bc.height == 1
        assert displaced == []

    def test_rollback_single_then_mine_again(self):
        bc, w = _setup_chain()
        _mine_block(bc, w)
        bc._rollback_to(1)
        assert bc.height == 0
        _mine_block(bc, w)
        assert bc.height == 1

    def test_rollback_latest_block_updates(self):
        bc, w = _setup_chain()
        _mine_blocks(bc, w, 2)
        blk1 = bc._get_block_by_index(1)
        bc._rollback_to(2)
        assert bc._latest_block is not None
        assert bc._latest_block.index == 1
        assert bc._latest_block.block_hash == blk1.block_hash

    @pytest.fixture(autouse=False)
    def _dynamic_fees(self, monkeypatch):
        monkeypatch.setattr("qbit_network.config.DYNAMIC_FEE_ACTIVATION_HEIGHT", 0)
        monkeypatch.setattr("qbit_network.core.blockchain.DYNAMIC_FEE_ACTIVATION_HEIGHT", 0)
        monkeypatch.setattr("qbit_network.core.consensus.DYNAMIC_FEE_ACTIVATION_HEIGHT", 0)

    @pytest.mark.usefixtures("_dynamic_fees")
    def test_rollback_empty_blocks_dynamic_mode(self):
        """Empty blocks are only allowed in dynamic fee mode."""
        bc, w = _setup_chain()
        bal_before = bc.get_balance(w.address)
        # Mine empty blocks (allowed with dynamic fees)
        for _ in range(3):
            blk = bc.produce_block(w.address, w.signing_sk)
            assert blk is not None
        assert bc.height == 3
        bc._rollback_to(1)
        assert bc.height == 0
        assert bc.get_balance(w.address) == bal_before


# ===========================================================================
# 9. Sender/recipient index cleanup
# ===========================================================================

class TestIndexCleanup:

    def test_sender_index_cleaned(self):
        bc, w = _setup_chain()
        bob = Wallet.generate()
        tx = Transaction.transfer(w.address, bob.address, 100, nonce=_next_nonce(bc, w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        _, tx_id = submit_and_mine(bc, w, tx)
        assert tx_id in bc._txs_by_sender.get(w.address, [])
        bc._rollback_to(1)
        assert tx_id not in bc._txs_by_sender.get(w.address, [])

    def test_recipient_index_cleaned(self):
        bc, w = _setup_chain()
        bob = Wallet.generate()
        tx = Transaction.transfer(w.address, bob.address, 100, nonce=_next_nonce(bc, w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        _, tx_id = submit_and_mine(bc, w, tx)
        assert tx_id in bc._txs_by_recipient.get(bob.address, [])
        bc._rollback_to(1)
        assert tx_id not in bc._txs_by_recipient.get(bob.address, [])

    def test_chain_tx_ids_cleaned(self):
        bc, w = _setup_chain()
        bob = Wallet.generate()
        tx = Transaction.transfer(w.address, bob.address, 100, nonce=_next_nonce(bc, w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        _, tx_id = submit_and_mine(bc, w, tx)
        assert tx_id in bc.consensus._chain_tx_ids
        bc._rollback_to(1)
        assert tx_id not in bc.consensus._chain_tx_ids


# ===========================================================================
# 10. Receipts and events rollback
# ===========================================================================

class TestReceiptsEventsRollback:

    def test_receipt_removed_on_rollback(self):
        bc, w = _setup_chain()
        bob = Wallet.generate()
        tx = Transaction.transfer(w.address, bob.address, 100, nonce=_next_nonce(bc, w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        _, tx_id = submit_and_mine(bc, w, tx)
        # Receipt should exist
        receipt = bc._receipts.get(tx_id)
        has_receipt = receipt is not None
        bc._rollback_to(1)
        assert bc._receipts.get(tx_id) is None

    def test_block_events_removed_on_rollback(self):
        bc, w = _setup_chain()
        bob = Wallet.generate()
        tx = Transaction.transfer(w.address, bob.address, 100, nonce=_next_nonce(bc, w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)
        bc._rollback_to(1)
        assert 1 not in bc._events_by_block


# ===========================================================================
# 11. Finalized height rollback
# ===========================================================================

class TestFinalizedHeight:

    def test_finalized_height_reset_on_rollback(self):
        bc, w = _setup_chain()
        _mine_blocks(bc, w, 3)
        # Manually set finalized to simulate BFT finality
        bc._finalized_height = 2
        bc._rollback_to(2)
        # Should reset since we rolled back past finalized
        assert bc._finalized_height <= 1


# ===========================================================================
# 12. Notarization counter rollback
# ===========================================================================

class TestNotarizationRollback:

    def test_notarization_count_decremented(self):
        bc, w = _setup_chain()
        doc_hash = sha3_256(b"doc1").hex()
        tx = Transaction.notarize(w.address, doc_hash, nonce=_next_nonce(bc, w.address))
        tx.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx)
        count_after = bc._notarization_count.get(w.address, 0)
        assert count_after >= 1
        bc._rollback_to(1)
        count_rolled = bc._notarization_count.get(w.address, 0)
        assert count_rolled == count_after - 1

    def test_multiple_notarizations_rollback(self):
        bc, w = _setup_chain()
        for i in range(3):
            doc_hash = sha3_256(f"doc{i}".encode()).hex()
            tx = Transaction.notarize(w.address, doc_hash, nonce=_next_nonce(bc, w.address))
            tx.sign(w.signing_sk, w.signing_pk)
            submit_and_mine(bc, w, tx)
        count_3 = bc._notarization_count.get(w.address, 0)
        bc._rollback_to(2)
        count_1 = bc._notarization_count.get(w.address, 0)
        assert count_1 == count_3 - 2
