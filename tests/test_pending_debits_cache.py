"""Tests for _pending_debits_cache consistency (TEC-1238 / R33-M01, R35-H01).

Verifies that the O(1) _pending_debits_cache stays in sync with the pool
across all mutation paths: submit, mine (drain), and revocation purge.
"""
import pytest
from qbit_network.core.blockchain import Blockchain
from qbit_network.core.transaction import Transaction, TxType
from qbit_network.core.wallet import Wallet
from qbit_network.core.fees import tx_weight


@pytest.fixture
def wallet():
    return Wallet.generate()


@pytest.fixture
def funded_chain(wallet):
    """Blockchain with financial layer active and genesis validator funded."""
    bc = Blockchain()
    bc.consensus.add_validator(wallet.address, wallet.signing_pk)
    bc.init_chain(wallet.address, wallet.signing_sk, validator_pk=wallet.signing_pk)
    bc.activate_financial_layer(wallet.address)
    return bc


def _brute_force_debits(bc, address):
    """Recompute pending debits by scanning the pool (the old O(n) way)."""
    from qbit_network import config as _config
    from qbit_network.config import TX_FEES
    dynamic_active = (bc._height + 1 >= _config.DYNAMIC_FEE_ACTIVATION_HEIGHT
                      and bc._height >= 0)
    total = 0
    for tx in bc.tx_pool:
        if tx.sender != address:
            continue
        if dynamic_active:
            w = tx_weight(tx.tx_type.value)
            fee = tx.max_fee_per_weight * w
        else:
            fee = TX_FEES.get(tx.tx_type.value, 0)
        total += fee
        if tx.tx_type == TxType.TRANSFER:
            total += tx.payload.get("amount", 0)
        elif tx.tx_type in (TxType.STAKE, TxType.DELEGATE):
            total += tx.payload.get("amount", 0)
    return total


def _assert_cache_consistent(bc):
    """Assert cache matches brute-force scan for every sender in pool."""
    senders = {tx.sender for tx in bc.tx_pool}
    for sender in senders:
        cached = bc._pending_debits_cache.get(sender, 0)
        expected = _brute_force_debits(bc, sender)
        assert cached == expected, (
            f"Cache mismatch for {sender[:16]}...: cached={cached}, expected={expected}")
    # Senders NOT in pool should have zero or no entry
    for addr, val in bc._pending_debits_cache.items():
        if addr not in senders:
            assert val == 0, f"Stale cache entry for {addr[:16]}...: {val}"


class TestPendingDebitsCacheSubmit:
    """Cache consistency after submit_tx."""

    def test_cache_empty_initially(self, funded_chain):
        assert funded_chain._pending_debits_cache == {}

    def test_cache_updated_on_notarize_submit(self, funded_chain, wallet):
        bc = funded_chain
        tx = Transaction.notarize(wallet.address, "a" * 64, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, _ = bc.submit_tx(tx)
        assert ok
        assert bc._pending_debits(wallet.address) > 0
        _assert_cache_consistent(bc)

    def test_cache_updated_on_transfer_submit(self, funded_chain, wallet):
        bc = funded_chain
        recipient = Wallet.generate()
        tx = Transaction.transfer(wallet.address, recipient.address, 1000, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, _ = bc.submit_tx(tx)
        assert ok
        cached = bc._pending_debits(wallet.address)
        expected = _brute_force_debits(bc, wallet.address)
        assert cached == expected
        assert cached >= 1000  # at least the transfer amount

    def test_cache_accumulates_multiple_txs(self, funded_chain, wallet):
        bc = funded_chain
        recipient = Wallet.generate()
        for i in range(5):
            tx = Transaction.transfer(wallet.address, recipient.address, 100, nonce=i)
            tx.sign(wallet.signing_sk, wallet.signing_pk)
            ok, _ = bc.submit_tx(tx)
            assert ok
        _assert_cache_consistent(bc)
        assert bc._pending_debits(wallet.address) >= 500


class TestPendingDebitsCacheDrain:
    """Cache consistency after block mining (_drain_pool)."""

    def test_cache_cleared_after_mining_all(self, funded_chain, wallet):
        bc = funded_chain
        tx = Transaction.notarize(wallet.address, "b" * 64, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        bc.submit_tx(tx)
        assert bc._pending_debits(wallet.address) > 0

        bc.produce_block(wallet.address, wallet.signing_sk)
        assert bc._pending_debits(wallet.address) == 0
        _assert_cache_consistent(bc)

    def test_cache_partial_drain(self, funded_chain, wallet):
        """Submit multiple txs, mine only one block (which takes some),
        verify cache still correct for remaining."""
        bc = funded_chain
        for i in range(3):
            tx = Transaction.notarize(wallet.address, f"{i:064x}", nonce=i)
            tx.sign(wallet.signing_sk, wallet.signing_pk)
            ok, _ = bc.submit_tx(tx)
            assert ok
        before = bc._pending_debits(wallet.address)
        assert before > 0

        bc.produce_block(wallet.address, wallet.signing_sk)
        _assert_cache_consistent(bc)

    def test_cache_after_multiple_blocks(self, funded_chain, wallet):
        bc = funded_chain
        for i in range(5):
            tx = Transaction.notarize(wallet.address, f"{i:064x}", nonce=i)
            tx.sign(wallet.signing_sk, wallet.signing_pk)
            bc.submit_tx(tx)
            bc.produce_block(wallet.address, wallet.signing_sk)
        assert bc._pending_debits(wallet.address) == 0
        _assert_cache_consistent(bc)


class TestPendingDebitsCacheRevocation:
    """Cache consistency after signing key revocation purges pool."""

    def test_cache_cleared_on_revocation(self, funded_chain, wallet):
        bc = funded_chain
        # Fund a second wallet generously
        w2 = Wallet.generate()
        fund_tx = Transaction.transfer(wallet.address, w2.address, 1_000_000_000, nonce=0)
        fund_tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, msg = bc.submit_tx(fund_tx)
        assert ok, f"fund failed: {msg}"
        bc.produce_block(wallet.address, wallet.signing_sk)

        # Submit tx from w2 into pool
        n_tx = Transaction.notarize(w2.address, "c" * 64, nonce=0)
        n_tx.sign(w2.signing_sk, w2.signing_pk)
        ok, msg = bc.submit_tx(n_tx)
        assert ok, f"notarize failed: {msg}"
        assert bc._pending_debits(w2.address) > 0

        # Revoke w2's signing key (this should purge w2's pool txs)
        nonce_w2 = bc.get_nonce(w2.address) + bc._pool_sender_count.get(w2.address, 0)
        revoke = Transaction.revoke_key(w2.address, "signing", "compromised", nonce=nonce_w2)
        revoke.sign(w2.signing_sk, w2.signing_pk)
        ok, msg = bc.submit_tx(revoke)
        assert ok, f"revoke failed: {msg}"
        bc.produce_block(wallet.address, wallet.signing_sk)

        # After revocation is mined, w2's pool txs should be purged
        assert bc._pending_debits(w2.address) == 0
        _assert_cache_consistent(bc)


class TestPendingDebitsCacheMultiSender:
    """Cache consistency with multiple senders."""

    def test_independent_senders(self, funded_chain, wallet):
        bc = funded_chain
        w2 = Wallet.generate()
        # Fund w2
        fund_tx = Transaction.transfer(wallet.address, w2.address, 10_000_000, nonce=0)
        fund_tx.sign(wallet.signing_sk, wallet.signing_pk)
        bc.submit_tx(fund_tx)
        bc.produce_block(wallet.address, wallet.signing_sk)

        # Both submit txs
        tx1 = Transaction.notarize(wallet.address, "d" * 64, nonce=1)
        tx1.sign(wallet.signing_sk, wallet.signing_pk)
        bc.submit_tx(tx1)

        tx2 = Transaction.notarize(w2.address, "e" * 64, nonce=0)
        tx2.sign(w2.signing_sk, w2.signing_pk)
        bc.submit_tx(tx2)

        _assert_cache_consistent(bc)
        assert bc._pending_debits(wallet.address) > 0
        assert bc._pending_debits(w2.address) > 0

        # Mine block — clears both
        bc.produce_block(wallet.address, wallet.signing_sk)
        _assert_cache_consistent(bc)


class TestPendingDebitsO1Lookup:
    """Verify _pending_debits() returns cached value without pool scan."""

    def test_returns_zero_for_unknown_address(self, funded_chain):
        assert funded_chain._pending_debits("qv1unknown") == 0

    def test_matches_calc_tx_debit(self, funded_chain, wallet):
        bc = funded_chain
        tx = Transaction.notarize(wallet.address, "f" * 64, nonce=0)
        tx.sign(wallet.signing_sk, wallet.signing_pk)
        bc.submit_tx(tx)
        expected = bc._calc_tx_debit(tx)
        assert bc._pending_debits(wallet.address) == expected


class TestPendingDebitsCacheFeeTransition:
    """R37-H01: Cache consistency across the dynamic-fee activation boundary.

    Verifies that _rebuild_pending_debits_cache uses the correct fee model
    when the chain crosses DYNAMIC_FEE_ACTIVATION_HEIGHT, even if called
    during or immediately after _append_block.
    """

    def test_cache_correct_at_activation_boundary(self, wallet, monkeypatch):
        """Submit TXs under static fees, produce block that crosses activation
        height, verify cache switches to dynamic fee model."""
        # Activation at block 2: blocks 0-1 use static, blocks 2+ use dynamic
        ACTIVATION = 2
        monkeypatch.setattr("qbit_network.config.DYNAMIC_FEE_ACTIVATION_HEIGHT",
                            ACTIVATION)
        monkeypatch.setattr("qbit_network.core.blockchain.DYNAMIC_FEE_ACTIVATION_HEIGHT",
                            ACTIVATION)
        monkeypatch.setattr("qbit_network.core.consensus.DYNAMIC_FEE_ACTIVATION_HEIGHT",
                            ACTIVATION)

        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk,
                      validator_pk=wallet.signing_pk)
        bc.activate_financial_layer(wallet.address)
        assert bc._height == 0  # genesis

        # Fund a second wallet so pool TXs aren't "self-tx" of the validator
        w2 = Wallet.generate()
        fund_tx = Transaction.transfer(wallet.address, w2.address,
                                       1_000_000_000, nonce=0)
        fund_tx.sign(wallet.signing_sk, wallet.signing_pk)
        ok, _ = bc.submit_tx(fund_tx)
        assert ok
        blk = bc.produce_block(wallet.address, wallet.signing_sk)
        assert blk is not None
        assert bc._height == 1

        # Now next block = 2 = ACTIVATION.  Submit pool TX from w2 with
        # dynamic fee fields.
        tx1 = Transaction.notarize(w2.address, "11" * 32,
                                   nonce=0,
                                   max_fee_per_weight=500)
        tx1.sign(w2.signing_sk, w2.signing_pk)
        ok, _ = bc.submit_tx(tx1)
        assert ok

        # Cache should reflect dynamic fees (next block = 2 >= ACTIVATION)
        _assert_cache_consistent(bc)
        debits_at_boundary = bc._pending_debits(w2.address)
        assert debits_at_boundary > 0

        # Produce block 2 (activation block) — drain rebuilds with dynamic
        blk2 = bc.produce_block(wallet.address, wallet.signing_sk)
        assert blk2 is not None
        assert bc._height == 2
        _assert_cache_consistent(bc)

    def test_calc_tx_debit_explicit_height_overrides(self, wallet, monkeypatch):
        """_calc_tx_debit with explicit next_block_height uses the supplied
        height rather than self._height + 1."""
        # Set activation at height 5 so we can test both sides of the boundary
        ACTIVATION = 5
        monkeypatch.setattr("qbit_network.config.DYNAMIC_FEE_ACTIVATION_HEIGHT",
                            ACTIVATION)
        monkeypatch.setattr("qbit_network.core.blockchain.DYNAMIC_FEE_ACTIVATION_HEIGHT",
                            ACTIVATION)
        monkeypatch.setattr("qbit_network.core.consensus.DYNAMIC_FEE_ACTIVATION_HEIGHT",
                            ACTIVATION)

        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk,
                      validator_pk=wallet.signing_pk)
        bc.activate_financial_layer(wallet.address)

        from qbit_network.config import TX_FEES

        tx = Transaction.notarize(wallet.address, "bb" * 32, nonce=0,
                                  max_fee_per_weight=500)
        tx.sign(wallet.signing_sk, wallet.signing_pk)

        # Explicit height >= ACTIVATION → dynamic fees
        debit_dyn = bc._calc_tx_debit(tx, next_block_height=ACTIVATION)
        # Explicit height < ACTIVATION → static fees
        debit_static = bc._calc_tx_debit(tx, next_block_height=ACTIVATION - 1)

        static_fee = TX_FEES.get(tx.tx_type.value, 0)
        assert debit_static == static_fee

        from qbit_network.core.fees import tx_weight
        w = tx_weight(tx.tx_type.value)
        expected_dyn = tx.max_fee_per_weight * w
        assert debit_dyn == expected_dyn
        # Verify that the two models produce different results
        assert debit_dyn != debit_static

    def test_pool_txs_survive_activation_boundary(self, wallet, monkeypatch):
        """R37-H01 regression: TXs submitted before activation that remain in
        pool after the chain crosses the activation boundary must have their
        pending debits recalculated using the dynamic fee model."""
        ACTIVATION = 3
        monkeypatch.setattr("qbit_network.config.DYNAMIC_FEE_ACTIVATION_HEIGHT",
                            ACTIVATION)
        monkeypatch.setattr("qbit_network.core.blockchain.DYNAMIC_FEE_ACTIVATION_HEIGHT",
                            ACTIVATION)
        monkeypatch.setattr("qbit_network.core.consensus.DYNAMIC_FEE_ACTIVATION_HEIGHT",
                            ACTIVATION)
        # Limit to 1 TX per block so w3's TX survives across the boundary
        monkeypatch.setattr("qbit_network.core.blockchain.MAX_TX_PER_BLOCK", 1)

        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk,
                      validator_pk=wallet.signing_pk)
        bc.activate_financial_layer(wallet.address)
        assert bc._height == 0

        # Fund w2 and w3 in separate blocks (1 TX per block limit)
        w2 = Wallet.generate()
        w3 = Wallet.generate()
        fund1 = Transaction.transfer(wallet.address, w2.address,
                                     2_000_000_000, nonce=0)
        fund1.sign(wallet.signing_sk, wallet.signing_pk)
        ok, _ = bc.submit_tx(fund1)
        assert ok
        blk1 = bc.produce_block(wallet.address, wallet.signing_sk)
        assert blk1 is not None
        assert bc._height == 1

        fund2 = Transaction.transfer(wallet.address, w3.address,
                                     2_000_000_000, nonce=1)
        fund2.sign(wallet.signing_sk, wallet.signing_pk)
        ok, _ = bc.submit_tx(fund2)
        assert ok

        # Submit TX from w2 — will be in pool alongside fund2
        tx_a = Transaction.notarize(w2.address, "aa" * 32, nonce=0,
                                    max_fee_per_weight=500)
        tx_a.sign(w2.signing_sk, w2.signing_pk)
        ok, _ = bc.submit_tx(tx_a)
        assert ok

        # Cache uses static fees (next=2 < ACTIVATION=3)
        from qbit_network.config import TX_FEES
        static_fee = TX_FEES.get("NOTARIZE", 0)
        assert bc._pending_debits(w2.address) == static_fee
        _assert_cache_consistent(bc)

        # Produce block 2: only fund2 is mined (1 TX limit), tx_a stays in pool
        blk2 = bc.produce_block(wallet.address, wallet.signing_sk)
        assert blk2 is not None
        assert bc._height == 2

        # Now next_block=3 = ACTIVATION.  tx_a remains in pool.
        # Cache MUST use dynamic fees for the surviving TX.
        assert len(bc.tx_pool) > 0, "tx_a should remain in pool"
        _assert_cache_consistent(bc)
        w = tx_weight(tx_a.tx_type.value)
        expected_dynamic = tx_a.max_fee_per_weight * w
        assert bc._pending_debits(w2.address) == expected_dynamic

    def test_drain_at_exact_activation_height(self, wallet, monkeypatch):
        """R37-H01 regression: when block at exactly ACTIVATION height is
        produced, _drain_pool must rebuild cache with dynamic fees for
        any remaining pool TXs."""
        ACTIVATION = 2
        monkeypatch.setattr("qbit_network.config.DYNAMIC_FEE_ACTIVATION_HEIGHT",
                            ACTIVATION)
        monkeypatch.setattr("qbit_network.core.blockchain.DYNAMIC_FEE_ACTIVATION_HEIGHT",
                            ACTIVATION)
        monkeypatch.setattr("qbit_network.core.consensus.DYNAMIC_FEE_ACTIVATION_HEIGHT",
                            ACTIVATION)

        bc = Blockchain()
        bc.consensus.add_validator(wallet.address, wallet.signing_pk)
        bc.init_chain(wallet.address, wallet.signing_sk,
                      validator_pk=wallet.signing_pk)
        bc.activate_financial_layer(wallet.address)
        assert bc._height == 0

        # Fund w2
        w2 = Wallet.generate()
        fund = Transaction.transfer(wallet.address, w2.address,
                                    2_000_000_000, nonce=0)
        fund.sign(wallet.signing_sk, wallet.signing_pk)
        ok, _ = bc.submit_tx(fund)
        assert ok
        blk1 = bc.produce_block(wallet.address, wallet.signing_sk)
        assert blk1 is not None
        assert bc._height == 1

        # Submit two TXs at height 1 (next=2=ACTIVATION)
        tx1 = Transaction.notarize(w2.address, "c1" * 32, nonce=0,
                                   max_fee_per_weight=300)
        tx1.sign(w2.signing_sk, w2.signing_pk)
        tx2 = Transaction.notarize(w2.address, "c2" * 32, nonce=1,
                                   max_fee_per_weight=400)
        tx2.sign(w2.signing_sk, w2.signing_pk)
        ok1, _ = bc.submit_tx(tx1)
        ok2, _ = bc.submit_tx(tx2)
        assert ok1 and ok2

        # Both use dynamic fees since next_block=2=ACTIVATION
        _assert_cache_consistent(bc)
        w = tx_weight("NOTARIZE")
        expected_both = tx1.max_fee_per_weight * w + tx2.max_fee_per_weight * w
        assert bc._pending_debits(w2.address) == expected_both

        # Produce activation block (height 2) — mines both TXs
        blk2 = bc.produce_block(wallet.address, wallet.signing_sk)
        assert blk2 is not None
        assert bc._height == 2

        # Pool drained, cache should be empty
        assert bc._pending_debits(w2.address) == 0
        _assert_cache_consistent(bc)
