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
