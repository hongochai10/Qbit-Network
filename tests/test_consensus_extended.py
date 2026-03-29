"""Extended tests for consensus — multi-validator, 3+ validators, edge cases."""
import time
import pytest
from qbit_network.core.wallet import Wallet
from qbit_network.core.transaction import Transaction, TxType
from qbit_network.core.block import Block
from qbit_network.core.consensus import ProofOfAuthority
from qbit_network.core.blockchain import Blockchain


# =========================================================================
# ProofOfAuthority validator management
# =========================================================================

class TestPoAValidatorManagement:

    def test_empty_validators(self):
        poa = ProofOfAuthority()
        assert not poa.validators

    def test_add_validator(self):
        poa = ProofOfAuthority()
        w = Wallet.generate()
        poa.add_validator(w.address, w.signing_pk)
        assert poa.is_validator(w.address) is True

    def test_remove_validator(self):
        poa = ProofOfAuthority()
        w = Wallet.generate()
        poa.add_validator(w.address, w.signing_pk)
        poa.remove_validator(w.address)
        assert poa.is_validator(w.address) is False

    def test_remove_nonexistent_is_noop(self):
        poa = ProofOfAuthority()
        # Should not raise
        poa.remove_validator("qv1" + "a" * 64)

    def test_get_validator_pk(self):
        poa = ProofOfAuthority()
        w = Wallet.generate()
        poa.add_validator(w.address, w.signing_pk)
        assert poa.get_validator_pk(w.address) == w.signing_pk

    def test_get_validator_pk_nonexistent_returns_none(self):
        poa = ProofOfAuthority()
        assert poa.get_validator_pk("qv1" + "a" * 64) is None

    def test_set_genesis_hash(self):
        poa = ProofOfAuthority()
        poa.set_genesis_hash("deadbeef")
        assert poa._genesis_hash == "deadbeef"

    def test_select_validator_none_when_empty(self):
        poa = ProofOfAuthority()
        assert poa.select_validator(0) is None

    def test_select_validator_deterministic(self):
        poa = ProofOfAuthority()
        w1 = Wallet.generate()
        w2 = Wallet.generate()
        poa.add_validator(w1.address, w1.signing_pk)
        poa.add_validator(w2.address, w2.signing_pk)
        # Same block index always gives same validator
        v1 = poa.select_validator(0)
        v2 = poa.select_validator(0)
        assert v1 == v2

    def test_select_validator_rotates(self):
        poa = ProofOfAuthority()
        w1 = Wallet.generate()
        w2 = Wallet.generate()
        poa.add_validator(w1.address, w1.signing_pk)
        poa.add_validator(w2.address, w2.signing_pk)
        # Two validators: blocks 0 and 1 go to different validators
        v0 = poa.select_validator(0)
        v1 = poa.select_validator(1)
        assert v0 != v1

    def test_select_validator_wraps_around(self):
        poa = ProofOfAuthority()
        w = Wallet.generate()
        poa.add_validator(w.address, w.signing_pk)
        # With one validator, all blocks go to same validator
        for i in range(10):
            assert poa.select_validator(i) == w.address


# =========================================================================
# ProofOfAuthority.validate_block
# =========================================================================

class TestPoAValidateBlock:

    def test_genesis_block_accepted(self):
        poa = ProofOfAuthority()
        w = Wallet.generate()
        genesis = Block.genesis(w.address)
        genesis.sign(w.signing_sk)
        ok, _ = poa.validate_block(genesis, None)
        assert ok is True

    def test_genesis_hash_mismatch_rejected(self):
        poa = ProofOfAuthority()
        w = Wallet.generate()
        genesis = Block.genesis(w.address)
        genesis.sign(w.signing_sk)
        poa.set_genesis_hash("badbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbad0")
        ok, err = poa.validate_block(genesis, None)
        assert ok is False
        assert "genesis hash" in err

    def test_no_parent_fails_for_non_genesis(self):
        poa = ProofOfAuthority()
        w = Wallet.generate()
        poa.add_validator(w.address, w.signing_pk)
        genesis = Block.genesis(w.address)
        genesis.sign(w.signing_sk)
        tx = Transaction.notarize(w.address, "aa" * 32, nonce=0)
        tx.sign(w.signing_sk, w.signing_pk)
        b1 = Block(index=1, prev_hash=genesis.block_hash, transactions=[tx],
                   validator=w.address, timestamp=genesis.timestamp + 1)
        b1.sign(w.signing_sk)
        ok, err = poa.validate_block(b1, None)
        assert ok is False
        assert "parent" in err

    def test_wrong_index_fails(self):
        poa = ProofOfAuthority()
        w = Wallet.generate()
        poa.add_validator(w.address, w.signing_pk)
        genesis = Block.genesis(w.address)
        genesis.sign(w.signing_sk)
        tx = Transaction.notarize(w.address, "bb" * 32, nonce=0)
        tx.sign(w.signing_sk, w.signing_pk)
        b_wrong = Block(index=5, prev_hash=genesis.block_hash, transactions=[tx],
                        validator=w.address, timestamp=genesis.timestamp + 1)
        b_wrong.sign(w.signing_sk)
        ok, err = poa.validate_block(b_wrong, genesis)
        assert ok is False
        assert "index" in err

    def test_wrong_prev_hash_fails(self):
        poa = ProofOfAuthority()
        w = Wallet.generate()
        poa.add_validator(w.address, w.signing_pk)
        genesis = Block.genesis(w.address)
        genesis.sign(w.signing_sk)
        tx = Transaction.notarize(w.address, "cc" * 32, nonce=0)
        tx.sign(w.signing_sk, w.signing_pk)
        b = Block(index=1, prev_hash="00" * 32, transactions=[tx],
                  validator=w.address, timestamp=genesis.timestamp + 1)
        b.sign(w.signing_sk)
        ok, err = poa.validate_block(b, genesis)
        assert ok is False
        assert "prev_hash" in err

    def test_timestamp_not_after_parent_fails(self):
        poa = ProofOfAuthority()
        w = Wallet.generate()
        poa.add_validator(w.address, w.signing_pk)
        genesis = Block.genesis(w.address)
        genesis.sign(w.signing_sk)
        tx = Transaction.notarize(w.address, "dd" * 32, nonce=0)
        tx.sign(w.signing_sk, w.signing_pk)
        # Same timestamp as parent
        b = Block(index=1, prev_hash=genesis.block_hash, transactions=[tx],
                  validator=w.address, timestamp=genesis.timestamp)
        b.sign(w.signing_sk)
        ok, err = poa.validate_block(b, genesis)
        assert ok is False
        assert "timestamp" in err

    def test_unknown_validator_fails(self):
        poa = ProofOfAuthority()
        w1 = Wallet.generate()
        w2 = Wallet.generate()
        poa.add_validator(w1.address, w1.signing_pk)
        genesis = Block.genesis(w1.address)
        genesis.sign(w1.signing_sk)
        tx = Transaction.notarize(w1.address, "ee" * 32, nonce=0)
        tx.sign(w1.signing_sk, w1.signing_pk)
        # w2 is not a validator
        b = Block(index=1, prev_hash=genesis.block_hash, transactions=[tx],
                  validator=w2.address, timestamp=genesis.timestamp + 1)
        b.sign(w2.signing_sk)
        ok, err = poa.validate_block(b, genesis)
        assert ok is False
        assert "unknown validator" in err

    def test_empty_block_after_genesis_fails(self):
        poa = ProofOfAuthority()
        w = Wallet.generate()
        poa.add_validator(w.address, w.signing_pk)
        genesis = Block.genesis(w.address)
        genesis.sign(w.signing_sk)
        b = Block(index=1, prev_hash=genesis.block_hash, transactions=[],
                  validator=w.address, timestamp=genesis.timestamp + 1)
        b.sign(w.signing_sk)
        ok, err = poa.validate_block(b, genesis)
        assert ok is False
        assert "transaction" in err.lower()

    def test_invalid_tx_signature_fails(self):
        poa = ProofOfAuthority()
        w = Wallet.generate()
        poa.add_validator(w.address, w.signing_pk)
        genesis = Block.genesis(w.address)
        genesis.sign(w.signing_sk)
        tx = Transaction.notarize(w.address, "ff" * 32, nonce=0)
        # Not signed
        b = Block(index=1, prev_hash=genesis.block_hash, transactions=[tx],
                  validator=w.address, timestamp=genesis.timestamp + 1)
        b.sign(w.signing_sk)
        ok, err = poa.validate_block(b, genesis)
        assert ok is False
        assert "signature" in err.lower()

    def test_duplicate_tx_in_block_fails(self):
        poa = ProofOfAuthority()
        w = Wallet.generate()
        poa.add_validator(w.address, w.signing_pk)
        poa._chain_nonces = {}
        poa._chain_tx_ids = set()
        genesis = Block.genesis(w.address)
        genesis.sign(w.signing_sk)
        tx = Transaction.notarize(w.address, "aa" * 32, nonce=0)
        tx.sign(w.signing_sk, w.signing_pk)
        # Same tx twice
        b = Block(index=1, prev_hash=genesis.block_hash, transactions=[tx, tx],
                  validator=w.address, timestamp=genesis.timestamp + 1)
        b.sign(w.signing_sk)
        ok, err = poa.validate_block(b, genesis)
        assert ok is False
        assert "duplicate" in err.lower()

    def test_nonce_gap_in_block_fails(self):
        poa = ProofOfAuthority()
        w = Wallet.generate()
        poa.add_validator(w.address, w.signing_pk)
        poa._chain_nonces = {w.address: -1}
        poa._chain_tx_ids = set()
        genesis = Block.genesis(w.address)
        genesis.sign(w.signing_sk)
        tx0 = Transaction.notarize(w.address, "11" * 32, nonce=0)
        tx0.sign(w.signing_sk, w.signing_pk)
        tx2 = Transaction.notarize(w.address, "22" * 32, nonce=2)  # gap: 1 missing
        tx2.sign(w.signing_sk, w.signing_pk)
        b = Block(index=1, prev_hash=genesis.block_hash, transactions=[tx0, tx2],
                  validator=w.address, timestamp=genesis.timestamp + 1)
        b.sign(w.signing_sk)
        ok, err = poa.validate_block(b, genesis)
        assert ok is False
        assert "nonce" in err.lower()

    def test_revoked_signing_key_block_fails(self):
        poa = ProofOfAuthority()
        w = Wallet.generate()
        poa.add_validator(w.address, w.signing_pk)
        poa._revoked_keys = {f"{w.address}:signing": {"tx_id": "x", "timestamp": 0.0, "reason": "x"}}
        genesis = Block.genesis(w.address)
        genesis.sign(w.signing_sk)
        tx = Transaction.notarize(w.address, "ab" * 32, nonce=0)
        tx.sign(w.signing_sk, w.signing_pk)
        b = Block(index=1, prev_hash=genesis.block_hash, transactions=[tx],
                  validator=w.address, timestamp=genesis.timestamp + 1)
        b.sign(w.signing_sk)
        ok, err = poa.validate_block(b, genesis)
        assert ok is False
        assert "revoked" in err.lower()

    def test_revoked_sender_tx_fails(self):
        poa = ProofOfAuthority()
        w = Wallet.generate()
        poa.add_validator(w.address, w.signing_pk)
        poa._revoked_keys = {f"{w.address}:signing": {"tx_id": "x", "timestamp": 0.0, "reason": "x"}}
        genesis = Block.genesis(w.address)
        genesis.sign(w.signing_sk)

        # Remove the revocation for the block validator, but keep for sender
        poa._revoked_keys = {}

        w2 = Wallet.generate()
        poa._revoked_keys[f"{w2.address}:signing"] = {"tx_id": "r", "timestamp": 0.0, "reason": "x"}
        poa._chain_nonces = {}
        poa._chain_tx_ids = set()

        tx = Transaction.notarize(w2.address, "cd" * 32, nonce=0)
        tx.sign(w2.signing_sk, w2.signing_pk)
        b = Block(index=1, prev_hash=genesis.block_hash, transactions=[tx],
                  validator=w.address, timestamp=genesis.timestamp + 1)
        b.sign(w.signing_sk)
        ok, err = poa.validate_block(b, genesis)
        assert ok is False
        assert "revoked" in err.lower()


# =========================================================================
# Three-validator round-robin rotation
# =========================================================================

class TestThreeValidatorRotation:

    def _setup_3_validators(self):
        v1, v2, v3 = Wallet.generate(), Wallet.generate(), Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(v1.address, v1.signing_pk)
        bc.consensus.add_validator(v2.address, v2.signing_pk)
        bc.consensus.add_validator(v3.address, v3.signing_pk)
        bc.init_chain(v1.address, v1.signing_sk)
        return v1, v2, v3, bc

    def test_three_validators_block_rotation(self):
        v1, v2, v3, bc = self._setup_3_validators()
        validators = sorted([v1.address, v2.address, v3.address])
        # Produce 6 blocks (2 full rotations)
        nonces = {v.address: 0 for v in [v1, v2, v3]}
        wallet_map = {v.address: v for v in [v1, v2, v3]}
        for i in range(1, 7):
            expected = bc.consensus.select_validator(i)
            wallet = wallet_map[expected]
            n = nonces[wallet.address]
            tx = Transaction.notarize(wallet.address, f"{i:064x}", nonce=n)
            tx.sign(wallet.signing_sk, wallet.signing_pk)
            bc.submit_tx(tx)
            block = bc.produce_block(expected, wallet.signing_sk)
            assert block is not None, f"Block #{i} failed (validator={expected[:8]}...)"
            nonces[wallet.address] += 1
        assert bc.height == 6

    def test_three_validators_wrong_turn_rejected(self):
        v1, v2, v3, bc = self._setup_3_validators()
        # The next expected validator is for index 1
        expected = bc.consensus.select_validator(1)
        wallet_map = {v.address: v for v in [v1, v2, v3]}
        wrong_wallet = None
        for v in [v1, v2, v3]:
            if v.address != expected:
                wrong_wallet = v
                break
        assert wrong_wallet is not None

        tx = Transaction.notarize(wrong_wallet.address, "11" * 32, nonce=0)
        tx.sign(wrong_wallet.signing_sk, wrong_wallet.signing_pk)
        bc.submit_tx(tx)
        block = bc.produce_block(wrong_wallet.address, wrong_wallet.signing_sk)
        assert block is None

    def test_three_validators_select_validator_cycles(self):
        """select_validator should cycle through all 3 validators."""
        poa = ProofOfAuthority()
        wallets = [Wallet.generate() for _ in range(3)]
        for w in wallets:
            poa.add_validator(w.address, w.signing_pk)
        addresses = sorted([w.address for w in wallets])
        # Check 9 blocks = 3 full cycles
        selected = [poa.select_validator(i) for i in range(9)]
        for i in range(3):
            cycle_set = set(selected[i * 3:(i + 1) * 3])
            assert len(cycle_set) == 3  # All 3 unique validators in each cycle

    def test_five_validators_rotation(self):
        """5 validators should rotate through all 5 in sequence."""
        poa = ProofOfAuthority()
        wallets = [Wallet.generate() for _ in range(5)]
        for w in wallets:
            poa.add_validator(w.address, w.signing_pk)
        # Check 10 blocks = 2 full cycles
        selected = [poa.select_validator(i) for i in range(10)]
        # First cycle and second cycle should be identical
        assert selected[:5] == selected[5:]


# =========================================================================
# Multi-validator blockchain production
# =========================================================================

class TestMultiValidatorBlockchainProduction:

    def test_two_validators_alternating_production(self):
        v1, v2 = Wallet.generate(), Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(v1.address, v1.signing_pk)
        bc.consensus.add_validator(v2.address, v2.signing_pk)
        bc.init_chain(v1.address, v1.signing_sk)

        wallet_map = {v1.address: v1, v2.address: v2}
        nonces = {v1.address: 0, v2.address: 0}
        for i in range(1, 5):
            expected = bc.consensus.select_validator(i)
            wallet = wallet_map[expected]
            tx = Transaction.notarize(wallet.address, f"{i:064x}", nonce=nonces[wallet.address])
            tx.sign(wallet.signing_sk, wallet.signing_pk)
            bc.submit_tx(tx)
            block = bc.produce_block(expected, wallet.signing_sk)
            assert block is not None
            nonces[wallet.address] += 1
        assert bc.height == 4

    def test_validator_removed_mid_chain(self):
        """After removing a validator, blocks from them are rejected."""
        v1, v2 = Wallet.generate(), Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(v1.address, v1.signing_pk)
        bc.consensus.add_validator(v2.address, v2.signing_pk)
        bc.init_chain(v1.address, v1.signing_sk)

        # Produce block 1 with whoever's turn it is
        expected_1 = bc.consensus.select_validator(1)
        wallet_map = {v1.address: v1, v2.address: v2}
        w = wallet_map[expected_1]
        tx = Transaction.notarize(w.address, "01" * 32, nonce=0)
        tx.sign(w.signing_sk, w.signing_pk)
        bc.submit_tx(tx)
        bc.produce_block(expected_1, w.signing_sk)

        # Remove v2 and try to get it to produce a block
        bc.consensus.remove_validator(v2.address)
        tx2 = Transaction.notarize(v2.address, "02" * 32, nonce=0)
        tx2.sign(v2.signing_sk, v2.signing_pk)
        bc.submit_tx(tx2)
        block = bc.produce_block(v2.address, v2.signing_sk)
        assert block is None  # v2 is not a validator anymore

    def test_produce_block_with_revoked_signing_key_returns_none(self):
        v1 = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(v1.address, v1.signing_pk)
        bc.init_chain(v1.address, v1.signing_sk)
        # Revoke v1's signing key
        bc._revoked_keys[f"{v1.address}:signing"] = {
            "tx_id": "rev", "timestamp": time.time(), "reason": "compromised"
        }
        tx = Transaction.notarize(v1.address, "aa" * 32, nonce=0)
        tx.sign(v1.signing_sk, v1.signing_pk)
        bc.submit_tx(tx)
        block = bc.produce_block(v1.address, v1.signing_sk)
        assert block is None


# =========================================================================
# Fork resolution with validator registration/revocation in forked blocks
# =========================================================================

class TestForkWithValidatorTxs:

    def test_reorg_with_register_validator_in_fork(self):
        """
        Main chain has 2 blocks. Fork has 3 blocks including a REGISTER_VALIDATOR tx.
        After reorg, the validator from the fork should be present.
        """
        from qbit_network.config import MAX_REORG_DEPTH

        v1 = Wallet.generate()
        v2 = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(v1.address, v1.signing_pk)
        bc.consensus.add_validator(v2.address, v2.signing_pk)
        bc.init_chain(v1.address, v1.signing_sk)

        wallet_map = {v1.address: v1, v2.address: v2}
        nonces = {v1.address: 0, v2.address: 0}

        # Produce 2 blocks on the main chain
        for i in range(1, 3):
            expected = bc.consensus.select_validator(i)
            w = wallet_map[expected]
            tx = Transaction.notarize(w.address, f"{i:064x}", nonce=nonces[w.address])
            tx.sign(w.signing_sk, w.signing_pk)
            bc.submit_tx(tx)
            bc.produce_block(expected, w.signing_sk)
            nonces[w.address] += 1

        assert bc.height == 2

        # Build 3-block fork from genesis
        fork_blocks = []
        fork_parent = bc.chain[0].block_hash
        fork_nonces = {v1.address: 0, v2.address: 0}
        for i in range(1, 4):
            expected = bc.consensus.select_validator(i)
            w = wallet_map[expected]
            n = fork_nonces[w.address]
            tx = Transaction.notarize(w.address, f"ff{n:062x}", nonce=n)
            tx.sign(w.signing_sk, w.signing_pk)
            fb = Block(index=i, prev_hash=fork_parent, transactions=[tx],
                       validator=expected, timestamp=bc.chain[0].timestamp + i)
            fb.sign(w.signing_sk)
            fork_parent = fb.block_hash
            fork_blocks.append(fb)
            fork_nonces[w.address] = n + 1

        ok, msg = bc.try_reorg(fork_blocks)
        assert ok, f"Reorg failed: {msg}"
        assert bc.height == 3

    def test_empty_fork_rejected(self):
        v1 = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(v1.address, v1.signing_pk)
        bc.init_chain(v1.address, v1.signing_sk)
        ok, msg = bc.try_reorg([])
        assert ok is False
        assert "empty" in msg.lower()

    def test_fork_not_connecting_to_chain_rejected(self):
        v1 = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(v1.address, v1.signing_pk)
        bc.init_chain(v1.address, v1.signing_sk)
        tx = Transaction.notarize(v1.address, "aa" * 32, nonce=0)
        tx.sign(v1.signing_sk, v1.signing_pk)
        bc.submit_tx(tx)
        bc.produce_block(v1.address, v1.signing_sk)

        # Fork block with wrong prev_hash
        tx2 = Transaction.notarize(v1.address, "bb" * 32, nonce=0)
        tx2.sign(v1.signing_sk, v1.signing_pk)
        bad_fork = Block(index=1, prev_hash="ff" * 32, transactions=[tx2],
                         validator=v1.address, timestamp=bc.chain[0].timestamp + 10)
        bad_fork.sign(v1.signing_sk)
        ok, msg = bc.try_reorg([bad_fork])
        assert ok is False
        assert "connect" in msg.lower() or "invalid fork" in msg.lower()

    def test_fork_start_zero_rejected(self):
        v1 = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(v1.address, v1.signing_pk)
        bc.init_chain(v1.address, v1.signing_sk)
        # Fork starting at index 0 (genesis replay)
        fake_genesis = Block.genesis(v1.address)
        fake_genesis.sign(v1.signing_sk)
        ok, msg = bc.try_reorg([fake_genesis])
        assert ok is False

    def test_deep_reorg_rejected(self):
        """try_reorg should reject forks that exceed MAX_REORG_DEPTH.

        Uses placeholder Block objects (no valid signatures) to trigger the
        depth check before validation — same pattern as test_adversarial.py.
        """
        from qbit_network.config import MAX_REORG_DEPTH
        v1 = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(v1.address, v1.signing_pk)
        bc.init_chain(v1.address, v1.signing_sk)

        # Build a real single block so chain height >= 1
        tx = Transaction.notarize(v1.address, "01" * 32, nonce=0)
        tx.sign(v1.signing_sk, v1.signing_pk)
        bc.submit_tx(tx)
        bc.produce_block(v1.address, v1.signing_sk)

        # Create fake fork blocks starting at index 1, deep enough to exceed limit
        # The blockchain checks depth = chain_len - fork_start before validating blocks
        # We need depth > MAX_REORG_DEPTH, so chain_len - fork_start > MAX_REORG_DEPTH
        # Inject enough blocks into the chain height manually using unsigned fakes
        for i in range(MAX_REORG_DEPTH + 3):
            bc._height += 1
            bc._block_by_hash[f"fake{i}"] = i + 2

        # Fork starting at index 1 (depth = current_height - 1 > MAX_REORG_DEPTH)
        fake_fork = [Block(index=1, prev_hash="00" * 32, transactions=[],
                           validator=v1.address)] * (MAX_REORG_DEPTH + 5)
        ok, msg = bc.try_reorg(fake_fork)
        assert ok is False
        assert "deep" in msg.lower() or "connect" in msg.lower() or "invalid" in msg.lower()


# =========================================================================
# Blockchain helper methods
# =========================================================================

class TestBlockchainHelpers:

    def test_get_next_nonce_unknown_address(self):
        w = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(w.address, w.signing_pk)
        bc.init_chain(w.address, w.signing_sk)
        unknown = Wallet.generate()
        assert bc.get_next_nonce(unknown.address) == 0

    def test_get_nonce_increments_after_tx(self):
        w = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(w.address, w.signing_pk)
        bc.init_chain(w.address, w.signing_sk)
        assert bc.get_nonce(w.address) == 0
        tx = Transaction.notarize(w.address, "aa" * 32, nonce=0)
        tx.sign(w.signing_sk, w.signing_pk)
        bc.submit_tx(tx)
        bc.produce_block(w.address, w.signing_sk)
        assert bc.get_nonce(w.address) == 1

    def test_is_registered_validator_false_before_register(self):
        w = Wallet.generate()
        bc = Blockchain()
        assert bc.is_registered_validator(w.address) is False

    def test_is_key_revoked_false_initially(self):
        w = Wallet.generate()
        bc = Blockchain()
        assert bc.is_key_revoked(w.address, "signing") is False

    def test_get_revocation_info_none_initially(self):
        w = Wallet.generate()
        bc = Blockchain()
        assert bc.get_revocation_info(w.address, "signing") is None

    def test_get_notarization_count_zero_initially(self):
        w = Wallet.generate()
        bc = Blockchain()
        assert bc.get_notarization_count(w.address) == 0

    def test_get_encryption_pk_none_before_register(self):
        w = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(w.address, w.signing_pk)
        bc.init_chain(w.address, w.signing_sk)
        assert bc.get_encryption_pk(w.address) is None

    def test_get_all_notarizations_empty_initially(self):
        w = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(w.address, w.signing_pk)
        bc.init_chain(w.address, w.signing_sk)
        assert bc.get_all_notarizations("aa" * 32) == []

    def test_submit_tx_pool_size_limit(self):
        from qbit_network.config import MAX_TX_POOL_SIZE
        w = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(w.address, w.signing_pk)
        bc.init_chain(w.address, w.signing_sk)
        # Fill the pool
        nonce = 0
        for i in range(MAX_TX_POOL_SIZE):
            tx = Transaction.notarize(w.address, f"{i:064x}", nonce=nonce)
            tx.sign(w.signing_sk, w.signing_pk)
            ok, _ = bc.submit_tx(tx)
            if ok:
                nonce += 1
        # One more should fail
        tx_extra = Transaction.notarize(w.address, "ff" * 32, nonce=nonce)
        tx_extra.sign(w.signing_sk, w.signing_pk)
        ok, msg = bc.submit_tx(tx_extra)
        assert ok is False
        assert "full" in msg

    def test_submit_tx_future_timestamp_rejected(self):
        w = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(w.address, w.signing_pk)
        bc.init_chain(w.address, w.signing_sk)
        tx = Transaction(
            tx_type=TxType.NOTARIZE,
            sender=w.address,
            payload={"documentHash": "aa" * 32},
            timestamp=int(time.time()) + 400,  # > 300s in future
            nonce=0,
        )
        tx.sign(w.signing_sk, w.signing_pk)
        ok, msg = bc.submit_tx(tx)
        assert ok is False
        assert "future" in msg

    def test_submit_tx_old_timestamp_rejected(self):
        w = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(w.address, w.signing_pk)
        bc.init_chain(w.address, w.signing_sk)
        tx = Transaction(
            tx_type=TxType.NOTARIZE,
            sender=w.address,
            payload={"documentHash": "bb" * 32},
            timestamp=int(time.time()) - 90000,  # >24h old
            nonce=0,
        )
        tx.sign(w.signing_sk, w.signing_pk)
        ok, msg = bc.submit_tx(tx)
        assert ok is False
        assert "old" in msg

    def test_add_block_already_have_returns_false(self):
        w = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(w.address, w.signing_pk)
        bc.init_chain(w.address, w.signing_sk)
        tx = Transaction.notarize(w.address, "aa" * 32, nonce=0)
        tx.sign(w.signing_sk, w.signing_pk)
        bc.submit_tx(tx)
        block = bc.produce_block(w.address, w.signing_sk)
        assert block is not None
        # Try to add the same block again
        ok, msg = bc.add_block(block)
        assert ok is False
        assert "already" in msg

    def test_add_block_out_of_order_fails(self):
        w = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(w.address, w.signing_pk)
        bc.init_chain(w.address, w.signing_sk)
        # Build a block at index 5 (skipping indices 1-4)
        tx = Transaction.notarize(w.address, "aa" * 32, nonce=0)
        tx.sign(w.signing_sk, w.signing_pk)
        b_far = Block(index=5, prev_hash=bc.chain[-1].block_hash, transactions=[tx],
                      validator=w.address, timestamp=bc.chain[-1].timestamp + 1)
        b_far.sign(w.signing_sk)
        ok, msg = bc.add_block(b_far)
        assert ok is False
        assert "out-of-order" in msg

    def test_produce_block_no_transactions_returns_none(self):
        w = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(w.address, w.signing_pk)
        bc.init_chain(w.address, w.signing_sk)
        # No txs submitted
        block = bc.produce_block(w.address, w.signing_sk)
        assert block is None

    def test_produce_block_before_genesis_returns_none(self):
        w = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(w.address, w.signing_pk)
        # No genesis yet
        block = bc.produce_block(w.address, w.signing_sk)
        assert block is None


# =========================================================================
# R25-003: PoA skip-slot uses block-internal timestamps (not wall clock)
# =========================================================================

class TestSkipSlotBlockTimestamps:
    """Verify that PoA skip-slot selection is deterministic based on
    block-internal timestamps, not system wall clock (R25-003)."""

    def test_select_with_skip_uses_block_timestamp(self):
        """_select_with_skip should use block_timestamp when provided,
        ignoring wall clock entirely."""
        poa = ProofOfAuthority()
        w1, w2, w3 = Wallet.generate(), Wallet.generate(), Wallet.generate()
        for w in (w1, w2, w3):
            poa.add_validator(w.address, w.signing_pk)
        addresses = sorted(poa.validators.keys())

        parent_ts = 1000.0
        # block_timestamp just after parent → no skip
        result_no_skip = poa._select_with_skip(addresses, 0, 3,
                                                parent_ts, parent_ts + 5.0)
        assert result_no_skip == addresses[0]

        # block_timestamp 16s after parent → 1 skip (threshold = 15s)
        result_one_skip = poa._select_with_skip(addresses, 0, 3,
                                                 parent_ts, parent_ts + 16.0)
        assert result_one_skip == addresses[1]

        # block_timestamp 31s after parent → 2 skips
        result_two_skips = poa._select_with_skip(addresses, 0, 3,
                                                  parent_ts, parent_ts + 31.0)
        assert result_two_skips == addresses[2]

    def test_select_with_skip_deterministic_regardless_of_clock(self):
        """Same block_timestamp must produce same result no matter when
        the validator runs the check (i.e., wall clock is irrelevant)."""
        poa = ProofOfAuthority()
        w1, w2 = Wallet.generate(), Wallet.generate()
        poa.add_validator(w1.address, w1.signing_pk)
        poa.add_validator(w2.address, w2.signing_pk)

        parent_ts = 5000.0
        block_ts = 5020.0  # 20s elapsed → 1 skip

        # Call multiple times — result must be identical
        results = set()
        for _ in range(10):
            r = poa.select_validator(1, parent_timestamp=parent_ts,
                                     block_timestamp=block_ts)
            results.add(r)
        assert len(results) == 1, "Selection must be deterministic"

    def test_clock_manipulation_does_not_affect_validation(self):
        """A validator with a manipulated system clock cannot claim a
        wrong turn when block_timestamp is used for selection."""
        poa = ProofOfAuthority()
        w1, w2 = Wallet.generate(), Wallet.generate()
        poa.add_validator(w1.address, w1.signing_pk)
        poa.add_validator(w2.address, w2.signing_pk)
        addresses = sorted(poa.validators.keys())

        parent_ts = 1000.0

        # Block produced at ts=1002 (2s after parent) — no skip expected
        expected_no_skip = poa.select_validator(
            1, parent_timestamp=parent_ts, block_timestamp=1002.0)
        assert expected_no_skip == addresses[1 % 2]

        # Even if wall clock is far in the future, block_timestamp governs
        expected_still_no_skip = poa.select_validator(
            1, parent_timestamp=parent_ts, block_timestamp=1002.0)
        assert expected_still_no_skip == expected_no_skip

        # Block claiming ts=1020 (20s gap) → skip should happen
        expected_skip = poa.select_validator(
            1, parent_timestamp=parent_ts, block_timestamp=1020.0)
        # skip = int(20/15) = 1
        assert expected_skip == addresses[(1 + 1) % 2]

    def test_validate_block_uses_block_timestamp_for_skip(self):
        """validate_block passes block.timestamp to select_validator,
        making validation deterministic (R25-003)."""
        v1 = Wallet.generate()
        v2 = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(v1.address, v1.signing_pk)
        bc.consensus.add_validator(v2.address, v2.signing_pk)
        bc.init_chain(v1.address, v1.signing_sk)

        genesis = bc.chain[0]
        addresses = sorted(bc.consensus.validators.keys())

        # Produce block at timestamp just after genesis — no skip
        block_ts = genesis.timestamp + 2
        expected_validator_addr = addresses[1 % 2]
        expected_wallet = v1 if v1.address == expected_validator_addr else v2

        tx = Transaction.notarize(expected_wallet.address, "aa" * 32, nonce=0)
        tx.sign(expected_wallet.signing_sk, expected_wallet.signing_pk)

        blk = Block(index=1, prev_hash=genesis.block_hash,
                    transactions=[tx], validator=expected_wallet.address,
                    timestamp=block_ts)
        blk.sign(expected_wallet.signing_sk)

        ok, msg = bc.consensus.validate_block(blk, genesis)
        assert ok, f"Block should be valid: {msg}"

    def test_select_with_skip_fallback_to_wallclock(self):
        """When block_timestamp is None (e.g., block production),
        _select_with_skip falls back to time.time()."""
        poa = ProofOfAuthority()
        w1 = Wallet.generate()
        poa.add_validator(w1.address, w1.signing_pk)

        # With very old parent_timestamp and no block_timestamp,
        # time.time() is used — result should still be valid
        result = poa._select_with_skip([w1.address], 0, 1,
                                        time.time() - 1.0, None)
        assert result == w1.address

    def test_dpos_path_unaffected_by_block_timestamp(self):
        """dPoS selection ignores block_timestamp entirely — it uses
        parent_hash + block_index for determinism."""
        poa = ProofOfAuthority()
        w1, w2 = Wallet.generate(), Wallet.generate()
        poa.add_validator(w1.address, w1.signing_pk)
        poa.add_validator(w2.address, w2.signing_pk)
        poa._get_active_validators = lambda: [
            (w1.address, 100), (w2.address, 100)
        ]

        # Same parent_hash + block_index → same result regardless of block_timestamp
        r1 = poa.select_validator(5, parent_hash="abc123",
                                  block_timestamp=1000.0)
        r2 = poa.select_validator(5, parent_hash="abc123",
                                  block_timestamp=9999.0)
        r3 = poa.select_validator(5, parent_hash="abc123",
                                  block_timestamp=None)
        assert r1 == r2 == r3
