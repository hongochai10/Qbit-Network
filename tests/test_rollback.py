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


    def test_multiblock_reorg_issue_mint_total_minted_non_negative(self):
        """R37-M02: total_minted must never go negative during multi-block reorg
        spanning ISSUE_TOKEN + MINT_TOKEN when MINT rollback happens before ISSUE."""
        bc, w = _setup_chain()
        bob = Wallet.generate()
        # Block 1: ISSUE token
        nonce = _next_nonce(bc, w.address)
        tx_issue = Transaction.issue_token(w.address, "Silver", "SLV", 6, nonce=nonce)
        tx_issue.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx_issue)
        token_id = _derive_token_id(w.address, "SLV", nonce)
        assert token_id in bc._token_registry
        # Block 2: MINT token
        tx_mint = Transaction.mint_token(
            w.address, bob.address, token_id, 10000,
            nonce=_next_nonce(bc, w.address))
        tx_mint.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx_mint)
        assert bc._token_registry[token_id]["total_minted"] == 10000
        assert bc.get_token_balance(token_id, bob.address) == 10000
        # Rollback both blocks (MINT rolled back first, then ISSUE)
        bc._rollback_to(1)
        # Token should be fully cleaned up
        assert token_id not in bc._token_registry
        assert bc.get_token_balance(token_id, bob.address) == 0

    def test_multiblock_reorg_multiple_mints_total_minted_clamped(self):
        """Verify total_minted is clamped to 0 even with multiple MINT blocks."""
        bc, w = _setup_chain()
        bob = Wallet.generate()
        # Block 1: ISSUE
        nonce = _next_nonce(bc, w.address)
        tx_issue = Transaction.issue_token(w.address, "Copper", "CPR", 2, nonce=nonce)
        tx_issue.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx_issue)
        token_id = _derive_token_id(w.address, "CPR", nonce)
        # Block 2: MINT 500
        tx_m1 = Transaction.mint_token(
            w.address, bob.address, token_id, 500,
            nonce=_next_nonce(bc, w.address))
        tx_m1.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx_m1)
        # Block 3: MINT 300
        tx_m2 = Transaction.mint_token(
            w.address, w.address, token_id, 300,
            nonce=_next_nonce(bc, w.address))
        tx_m2.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx_m2)
        assert bc._token_registry[token_id]["total_minted"] == 800
        # Rollback blocks 3, 2, 1 — mints rolled back before issue
        bc._rollback_to(1)
        assert token_id not in bc._token_registry
        assert bc.get_token_balance(token_id, bob.address) == 0
        assert bc.get_token_balance(token_id, w.address) >= 0  # no negative


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


# ===========================================================================
# 13. TRANSFER rollback balance conservation (H-02 / R33)
# ===========================================================================

class TestTransferRollbackConservation:
    """Tests for H-02: rollback must not create or destroy QBIT."""

    # Legacy transfer fee from config
    XFER_FEE = TX_FEES.get("TRANSFER", 0)

    def _total_balances(self, bc):
        """Sum all balances including negatives."""
        return sum(bc._balances.values())

    def test_recipient_spent_funds_before_rollback(self):
        """H-02 exploit: Alice->Bob, Bob spends, rollback should not create QBIT."""
        bc, w = _setup_chain()
        alice = Wallet.generate()
        bob = Wallet.generate()
        carol = Wallet.generate()
        fee = self.XFER_FEE

        # Fund Alice and Bob with enough for transfers + fees
        amt = 100_000_000_000
        bc._credit(alice.address, amt + fee)
        bc._credit(bob.address, fee)  # Bob needs fee for his transfer
        supply_before = self._total_balances(bc)

        # Block 1: Alice sends 100 QBIT to Bob
        tx1 = Transaction.transfer(alice.address, bob.address, amt,
                                   nonce=_next_nonce(bc, alice.address))
        tx1.sign(alice.signing_sk, alice.signing_pk)
        submit_and_mine(bc, w, tx1)

        # Block 2: Bob sends 100 QBIT to Carol (spends the transfer)
        tx2 = Transaction.transfer(bob.address, carol.address, amt,
                                   nonce=_next_nonce(bc, bob.address))
        tx2.sign(bob.signing_sk, bob.signing_pk)
        submit_and_mine(bc, w, tx2)

        assert bc.get_balance(bob.address) == 0  # Bob spent it all

        # Rollback both blocks
        bc._rollback_to(1)

        # Conservation: total supply must match what it was before the blocks
        supply_after = self._total_balances(bc)
        assert supply_after == supply_before, (
            f"Supply mismatch: before={supply_before}, after={supply_after}, "
            f"diff={supply_after - supply_before}")

        # Alice should have her funds back
        assert bc.get_balance(alice.address) == amt + fee
        # Bob should have just his fee money back
        assert bc.get_balance(bob.address) == fee
        assert bc.get_balance(carol.address) == 0

        # No negative balances
        for addr, bal in bc._balances.items():
            assert bal >= 0, f"Negative balance for {addr}: {bal}"

    def test_multi_hop_transfer_rollback(self):
        """A->B->C->D, rollback all: total supply conserved."""
        bc, w = _setup_chain()
        alice = Wallet.generate()
        bob = Wallet.generate()
        carol = Wallet.generate()
        dave = Wallet.generate()
        fee = self.XFER_FEE
        amt = 100_000_000_000

        # Each sender needs amount + fee
        bc._credit(alice.address, amt + fee)
        bc._credit(bob.address, fee)
        bc._credit(carol.address, fee)
        supply_before = self._total_balances(bc)

        # Block 1: Alice -> Bob
        tx1 = Transaction.transfer(alice.address, bob.address, amt,
                                   nonce=_next_nonce(bc, alice.address))
        tx1.sign(alice.signing_sk, alice.signing_pk)
        submit_and_mine(bc, w, tx1)

        # Block 2: Bob -> Carol
        tx2 = Transaction.transfer(bob.address, carol.address, amt,
                                   nonce=_next_nonce(bc, bob.address))
        tx2.sign(bob.signing_sk, bob.signing_pk)
        submit_and_mine(bc, w, tx2)

        # Block 3: Carol -> Dave
        tx3 = Transaction.transfer(carol.address, dave.address, amt,
                                   nonce=_next_nonce(bc, carol.address))
        tx3.sign(carol.signing_sk, carol.signing_pk)
        submit_and_mine(bc, w, tx3)

        assert bc.get_balance(dave.address) == amt

        # Rollback all 3 blocks
        bc._rollback_to(1)

        supply_after = self._total_balances(bc)
        assert supply_after == supply_before, (
            f"Supply mismatch: before={supply_before}, after={supply_after}")

        assert bc.get_balance(alice.address) == amt + fee
        assert bc.get_balance(bob.address) == fee
        assert bc.get_balance(carol.address) == fee
        assert bc.get_balance(dave.address) == 0

        for addr, bal in bc._balances.items():
            assert bal >= 0, f"Negative balance for {addr}: {bal}"

    def test_partial_spend_rollback_conservation(self):
        """Alice->Bob 100, Bob spends 60 to Carol, rollback both."""
        bc, w = _setup_chain()
        alice = Wallet.generate()
        bob = Wallet.generate()
        carol = Wallet.generate()
        fee = self.XFER_FEE
        amt = 100_000_000_000
        spend = 60_000_000_000

        bc._credit(alice.address, amt + fee)
        bc._credit(bob.address, fee)
        supply_before = self._total_balances(bc)

        # Block 1: Alice -> Bob 100
        tx1 = Transaction.transfer(alice.address, bob.address, amt,
                                   nonce=_next_nonce(bc, alice.address))
        tx1.sign(alice.signing_sk, alice.signing_pk)
        submit_and_mine(bc, w, tx1)

        # Block 2: Bob -> Carol 60 (partial spend)
        tx2 = Transaction.transfer(bob.address, carol.address, spend,
                                   nonce=_next_nonce(bc, bob.address))
        tx2.sign(bob.signing_sk, bob.signing_pk)
        submit_and_mine(bc, w, tx2)

        assert bc.get_balance(bob.address) == amt - spend  # 40 QBIT left

        bc._rollback_to(1)

        supply_after = self._total_balances(bc)
        assert supply_after == supply_before
        assert bc.get_balance(alice.address) == amt + fee
        assert bc.get_balance(bob.address) == fee
        assert bc.get_balance(carol.address) == 0

    def test_diamond_spend_rollback(self):
        """Alice sends to Bob AND Carol, both send to Dave, rollback all."""
        bc, w = _setup_chain()
        alice = Wallet.generate()
        bob = Wallet.generate()
        carol = Wallet.generate()
        dave = Wallet.generate()
        fee = self.XFER_FEE
        amt = 100_000_000_000

        # Alice needs 2*amt + 2*fee; Bob and Carol need fee each
        bc._credit(alice.address, 2 * amt + 2 * fee)
        bc._credit(bob.address, fee)
        bc._credit(carol.address, fee)
        supply_before = self._total_balances(bc)

        # Block 1: Alice -> Bob 100
        tx1 = Transaction.transfer(alice.address, bob.address, amt,
                                   nonce=_next_nonce(bc, alice.address))
        tx1.sign(alice.signing_sk, alice.signing_pk)
        submit_and_mine(bc, w, tx1)

        # Block 2: Alice -> Carol 100
        tx2 = Transaction.transfer(alice.address, carol.address, amt,
                                   nonce=_next_nonce(bc, alice.address))
        tx2.sign(alice.signing_sk, alice.signing_pk)
        submit_and_mine(bc, w, tx2)

        # Block 3: Bob -> Dave 100
        tx3 = Transaction.transfer(bob.address, dave.address, amt,
                                   nonce=_next_nonce(bc, bob.address))
        tx3.sign(bob.signing_sk, bob.signing_pk)
        submit_and_mine(bc, w, tx3)

        # Block 4: Carol -> Dave 100
        tx4 = Transaction.transfer(carol.address, dave.address, amt,
                                   nonce=_next_nonce(bc, carol.address))
        tx4.sign(carol.signing_sk, carol.signing_pk)
        submit_and_mine(bc, w, tx4)

        assert bc.get_balance(dave.address) == 2 * amt

        bc._rollback_to(1)

        supply_after = self._total_balances(bc)
        assert supply_after == supply_before
        assert bc.get_balance(alice.address) == 2 * amt + 2 * fee
        assert bc.get_balance(bob.address) == fee
        assert bc.get_balance(carol.address) == fee
        assert bc.get_balance(dave.address) == 0

        for addr, bal in bc._balances.items():
            assert bal >= 0, f"Negative balance for {addr}: {bal}"


# ===========================================================================
# R36-L04: _rebuild_state_trie must not crash on temporary negative balances
# ===========================================================================

class TestRebuildStateTrieNegativeBalance:
    """Regression test for R36-L04.

    During multi-block rollback, _rollback_debit allows temporary negative
    balances.  _rebuild_state_trie is called per-block and must tolerate
    negative balances without raising OverflowError.
    """

    def test_multiblock_rollback_no_crash_on_negative_balance(self):
        """3-block reorg with cross-block transfers — negative intermediates."""
        bc, w = _setup_chain()
        alice = Wallet.generate()
        bob = Wallet.generate()
        fee = TX_FEES.get(TxType.TRANSFER.value, 0)
        amt = 500 * QUBIT_PER_QBIT

        # Block 1: validator -> Alice (amt + fee so Alice can pay fee on next tx)
        tx1 = Transaction.transfer(w.address, alice.address, amt + fee,
                                   nonce=_next_nonce(bc, w.address))
        tx1.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx1)

        # Block 2: Alice -> Bob (Alice sends amt, pays fee from the extra)
        tx2 = Transaction.transfer(alice.address, bob.address, amt,
                                   nonce=_next_nonce(bc, alice.address))
        tx2.sign(alice.signing_sk, alice.signing_pk)
        submit_and_mine(bc, w, tx2)

        # Block 3: Bob -> validator (Bob sends amt, fee deducted from received)
        tx3 = Transaction.transfer(bob.address, w.address, amt - fee,
                                   nonce=_next_nonce(bc, bob.address))
        tx3.sign(bob.signing_sk, bob.signing_pk)
        submit_and_mine(bc, w, tx3)

        assert bc.height == 3

        # Force no snapshots so _rebuild_state_trie is called during rollback
        bc._state_snapshots.clear()

        # This must not raise OverflowError (the R36-L04 bug)
        bc._rollback_to(1)

        assert bc.height == 0
        # After full rollback to genesis, all balances must be non-negative
        for addr, bal in bc._balances.items():
            assert bal >= 0, f"Negative balance for {addr}: {bal}"

    def test_state_trie_correct_after_multiblock_rollback(self):
        """State trie root must match a fresh rebuild after rollback completes."""
        bc, w = _setup_chain()
        alice = Wallet.generate()
        bob = Wallet.generate()
        fee = TX_FEES.get(TxType.TRANSFER.value, 0)
        amt = 200 * QUBIT_PER_QBIT

        root_at_genesis = bc._state_trie.root()

        # Mine 3 blocks with cross-block transfers
        tx1 = Transaction.transfer(w.address, alice.address, amt + fee,
                                   nonce=_next_nonce(bc, w.address))
        tx1.sign(w.signing_sk, w.signing_pk)
        submit_and_mine(bc, w, tx1)

        tx2 = Transaction.transfer(alice.address, bob.address, amt,
                                   nonce=_next_nonce(bc, alice.address))
        tx2.sign(alice.signing_sk, alice.signing_pk)
        submit_and_mine(bc, w, tx2)

        tx3 = Transaction.transfer(bob.address, w.address, amt - fee,
                                   nonce=_next_nonce(bc, bob.address))
        tx3.sign(bob.signing_sk, bob.signing_pk)
        submit_and_mine(bc, w, tx3)

        bc._state_snapshots.clear()
        bc._rollback_to(1)

        # Rebuild trie from scratch and compare
        bc._rebuild_state_trie()
        root_after_rollback = bc._state_trie.root()

        assert root_after_rollback == root_at_genesis
