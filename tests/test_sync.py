"""Tests for SyncManager and sync protocol."""
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from qbit_network.network.sync import SyncManager, SyncState, _TRANSITIONS
from qbit_network.network.sync_messages import (
    MSG_GET_CHAIN_INFO, MSG_CHAIN_INFO,
    MSG_GET_HEADERS, MSG_HEADERS,
    MSG_GET_BLOCK_BODIES, MSG_BLOCK_BODIES,
    header_from_block_dict, validate_header_dict,
)
from qbit_network.config import (
    SYNC_BATCH_SIZE, SYNC_BLOCK_BATCH, SYNC_TIMEOUT,
    SYNC_THRESHOLD, SYNC_MAX_PARALLEL, SYNC_MAX_RETRIES,
    SYNC_CHECKPOINT_INTERVAL, SYNC_CROSS_VERIFY_PEERS,
    MAX_REORG_DEPTH,
)


# ================================================================
# Fixtures
# ================================================================

def _make_mock_blockchain(height=-1):
    bc = MagicMock()
    bc.height = height
    bc.latest_block = None
    bc._validator_registry = {}
    bc._current_epoch = 0
    bc.get_block_headers_for_sync = MagicMock(return_value=[])
    bc.get_blocks_for_sync = MagicMock(return_value=[])
    bc.validate_header_chain = MagicMock(return_value=(True, ""))
    bc.add_block = MagicMock(return_value=(True, ""))
    return bc


def _make_mock_peer(addr="1.2.3.4:9000", height=100, connected=True):
    peer = MagicMock()
    peer.addr = addr
    peer.host = addr.split(":")[0]
    peer.port = int(addr.split(":")[1])
    peer.height = height
    peer.connected = connected
    peer.send = AsyncMock()
    peer.send_encrypted = AsyncMock()
    return peer


def _make_mock_p2p(peers=None):
    p2p = MagicMock()
    p2p.peers = {}
    if peers:
        for p in peers:
            p2p.peers[p.addr] = p
    p2p.peer_count = MagicMock(return_value=len(p2p.peers))
    p2p.best_peer_height = MagicMock(
        return_value=max((p.height for p in p2p.peers.values()), default=-1)
    )
    p2p.reputation = MagicMock()
    p2p.reputation.score = MagicMock(return_value=100)
    p2p.reputation.record = MagicMock()
    return p2p


def _make_header(index, prev_hash="0" * 64, validator="qv1validator"):
    h = f"{index:064x}"
    return {
        "index": index,
        "timestamp": 1000000 + index * 5,
        "prevHash": prev_hash,
        "hash": h,
        "stateRoot": "a" * 64,
        "receiptsRoot": "b" * 64,
        "validator": validator,
        "signature": "cc" * 3309,
        "txCount": 5,
        "baseFee": 10,
        "merkleRoot": "d" * 64,
    }


def _make_header_chain(start, count, genesis_hash="0" * 64, validator="qv1validator"):
    headers = []
    prev = genesis_hash
    for i in range(start, start + count):
        hdr = _make_header(i, prev_hash=prev, validator=validator)
        headers.append(hdr)
        prev = hdr["hash"]
    return headers


# ================================================================
# Config constants tests
# ================================================================

class TestSyncConfig:
    def test_sync_batch_size(self):
        assert SYNC_BATCH_SIZE == 500

    def test_sync_block_batch(self):
        assert SYNC_BLOCK_BATCH == 100

    def test_sync_timeout(self):
        assert SYNC_TIMEOUT == 30

    def test_sync_threshold(self):
        assert SYNC_THRESHOLD == 3

    def test_sync_max_parallel(self):
        assert SYNC_MAX_PARALLEL == 4

    def test_sync_max_retries(self):
        assert SYNC_MAX_RETRIES == 3

    def test_sync_checkpoint_interval(self):
        assert SYNC_CHECKPOINT_INTERVAL == 100


# ================================================================
# State machine tests
# ================================================================

class TestSyncStateMachine:
    def test_initial_state_is_idle(self):
        bc = _make_mock_blockchain()
        p2p = _make_mock_p2p()
        sm = SyncManager(bc, p2p)
        assert sm.state == SyncState.IDLE

    def test_valid_transitions(self):
        """All valid transitions should succeed."""
        bc = _make_mock_blockchain()
        p2p = _make_mock_p2p()
        sm = SyncManager(bc, p2p)

        # IDLE -> HEADER_SYNC
        assert sm._transition(SyncState.HEADER_SYNC) is True
        assert sm.state == SyncState.HEADER_SYNC

        # HEADER_SYNC -> BLOCK_SYNC
        assert sm._transition(SyncState.BLOCK_SYNC) is True
        assert sm.state == SyncState.BLOCK_SYNC

        # BLOCK_SYNC -> SYNCED
        assert sm._transition(SyncState.SYNCED) is True
        assert sm.state == SyncState.SYNCED

        # SYNCED -> HEADER_SYNC (re-sync)
        assert sm._transition(SyncState.HEADER_SYNC) is True
        assert sm.state == SyncState.HEADER_SYNC

    def test_invalid_transition_idle_to_synced(self):
        bc = _make_mock_blockchain()
        p2p = _make_mock_p2p()
        sm = SyncManager(bc, p2p)
        assert sm._transition(SyncState.SYNCED) is False
        assert sm.state == SyncState.IDLE

    def test_invalid_transition_idle_to_block_sync(self):
        bc = _make_mock_blockchain()
        p2p = _make_mock_p2p()
        sm = SyncManager(bc, p2p)
        assert sm._transition(SyncState.BLOCK_SYNC) is False
        assert sm.state == SyncState.IDLE

    def test_header_sync_to_idle_on_disconnect(self):
        bc = _make_mock_blockchain()
        p2p = _make_mock_p2p()
        sm = SyncManager(bc, p2p)
        sm._transition(SyncState.HEADER_SYNC)
        assert sm._transition(SyncState.IDLE) is True

    def test_block_sync_to_header_sync_on_invalid_block(self):
        bc = _make_mock_blockchain()
        p2p = _make_mock_p2p()
        sm = SyncManager(bc, p2p)
        sm._transition(SyncState.HEADER_SYNC)
        sm._transition(SyncState.BLOCK_SYNC)
        assert sm._transition(SyncState.HEADER_SYNC) is True

    def test_synced_to_idle_on_all_disconnect(self):
        bc = _make_mock_blockchain()
        p2p = _make_mock_p2p()
        sm = SyncManager(bc, p2p)
        sm._transition(SyncState.HEADER_SYNC)
        sm._transition(SyncState.BLOCK_SYNC)
        sm._transition(SyncState.SYNCED)
        assert sm._transition(SyncState.IDLE) is True

    def test_all_transitions_covered(self):
        """Verify the transitions dict covers all states."""
        for state in SyncState:
            assert state in _TRANSITIONS


# ================================================================
# is_syncing tests
# ================================================================

class TestIsSyncing:
    def test_idle_not_syncing(self):
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        assert sm.is_syncing() is False

    def test_header_sync_is_syncing(self):
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        sm._transition(SyncState.HEADER_SYNC)
        assert sm.is_syncing() is True

    def test_block_sync_is_syncing(self):
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        sm._transition(SyncState.HEADER_SYNC)
        sm._transition(SyncState.BLOCK_SYNC)
        assert sm.is_syncing() is True

    def test_synced_not_syncing(self):
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        sm._transition(SyncState.HEADER_SYNC)
        sm._transition(SyncState.BLOCK_SYNC)
        sm._transition(SyncState.SYNCED)
        assert sm.is_syncing() is False


# ================================================================
# Progress reporting tests
# ================================================================

class TestProgress:
    def test_initial_progress(self):
        bc = _make_mock_blockchain(height=-1)
        sm = SyncManager(bc, _make_mock_p2p())
        p = sm.progress
        assert p["state"] == "idle"
        assert p["local_height"] == -1
        assert p["target_height"] == -1

    def test_progress_during_sync(self):
        bc = _make_mock_blockchain(height=50)
        sm = SyncManager(bc, _make_mock_p2p())
        sm._target_height = 100
        sm._transition(SyncState.HEADER_SYNC)
        p = sm.progress
        assert p["state"] == "header_sync"
        assert p["local_height"] == 50
        assert p["target_height"] == 100
        assert p["percent"] == 50.0

    def test_progress_fully_synced(self):
        bc = _make_mock_blockchain(height=100)
        sm = SyncManager(bc, _make_mock_p2p())
        sm._target_height = 100
        p = sm.progress
        assert p["percent"] == 100.0

    def test_progress_includes_stats(self):
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        p = sm.progress
        assert "stats" in p
        assert "headers_downloaded" in p["stats"]
        assert "blocks_downloaded" in p["stats"]


# ================================================================
# Peer selection tests
# ================================================================

class TestPeerSelection:
    def test_select_highest_peer(self):
        peer1 = _make_mock_peer("1.1.1.1:9000", height=50)
        peer2 = _make_mock_peer("2.2.2.2:9000", height=100)
        p2p = _make_mock_p2p([peer1, peer2])
        sm = SyncManager(_make_mock_blockchain(), p2p)
        best = sm._select_sync_source()
        assert best.addr == "2.2.2.2:9000"

    def test_select_no_peers(self):
        p2p = _make_mock_p2p([])
        sm = SyncManager(_make_mock_blockchain(), p2p)
        assert sm._select_sync_source() is None

    def test_skip_disconnected_peers(self):
        peer1 = _make_mock_peer("1.1.1.1:9000", height=100, connected=False)
        peer2 = _make_mock_peer("2.2.2.2:9000", height=50, connected=True)
        p2p = _make_mock_p2p([peer1, peer2])
        sm = SyncManager(_make_mock_blockchain(), p2p)
        best = sm._select_sync_source()
        assert best.addr == "2.2.2.2:9000"

    def test_get_sync_peers_limited(self):
        peers = [_make_mock_peer(f"1.1.1.{i}:9000", height=100) for i in range(10)]
        p2p = _make_mock_p2p(peers)
        sm = SyncManager(_make_mock_blockchain(), p2p)
        sync_peers = sm._get_sync_peers()
        assert len(sync_peers) <= SYNC_MAX_PARALLEL


# ================================================================
# Response handler tests
# ================================================================

class TestResponseHandlers:
    def test_on_headers_response_resolves_future(self):
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        loop = asyncio.new_event_loop()
        fut = loop.create_future()
        request_id = "test123"
        sm._pending_responses[request_id] = fut

        data = {"headers": [_make_header(1)], "request_id": request_id}
        sm.on_headers_response(data)

        assert fut.done()
        assert fut.result() == data
        loop.close()

    def test_on_block_bodies_response_resolves_future(self):
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        loop = asyncio.new_event_loop()
        fut = loop.create_future()
        request_id = "test456"
        sm._pending_responses[request_id] = fut

        data = {"blocks": [], "request_id": request_id}
        sm.on_block_bodies_response(data)

        assert fut.done()
        assert fut.result() == data
        loop.close()

    def test_unknown_request_id_ignored(self):
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        # Should not raise
        sm.on_headers_response({"headers": [], "request_id": "unknown"})
        sm.on_block_bodies_response({"blocks": [], "request_id": "unknown"})

    def test_on_peer_status_updates_target(self):
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        sm._target_height = 50
        sm.on_peer_status("1.1.1.1:9000", 100)
        assert sm._target_height == 100

    def test_on_peer_status_no_downgrade(self):
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        sm._target_height = 100
        sm.on_peer_status("1.1.1.1:9000", 50)
        assert sm._target_height == 100


# ================================================================
# sync_messages.py tests
# ================================================================

class TestSyncMessages:
    def test_header_from_block_dict(self):
        block = {
            "index": 5,
            "timestamp": 1000025,
            "prevHash": "a" * 64,
            "hash": "b" * 64,
            "stateRoot": "c" * 64,
            "receiptsRoot": "d" * 64,
            "validator": "qv1test",
            "signature": "ee" * 100,
            "transactions": [{"id": "1"}, {"id": "2"}],
            "baseFee": 10,
            "merkleRoot": "f" * 64,
        }
        hdr = header_from_block_dict(block)
        assert hdr["index"] == 5
        assert hdr["txCount"] == 2
        assert "transactions" not in hdr

    def test_header_from_block_dict_with_txcount(self):
        block = {
            "index": 5,
            "timestamp": 1000025,
            "prevHash": "a" * 64,
            "hash": "b" * 64,
            "validator": "qv1test",
            "signature": "ee" * 100,
            "txCount": 10,
            "baseFee": 0,
            "merkleRoot": "f" * 64,
        }
        hdr = header_from_block_dict(block)
        assert hdr["txCount"] == 10

    def test_validate_header_dict_valid(self):
        hdr = _make_header(1)
        assert validate_header_dict(hdr) is True

    def test_validate_header_dict_missing_field(self):
        hdr = _make_header(1)
        del hdr["hash"]
        assert validate_header_dict(hdr) is False

    def test_validate_header_dict_negative_index(self):
        hdr = _make_header(1)
        hdr["index"] = -1
        assert validate_header_dict(hdr) is False

    def test_validate_header_dict_short_hash(self):
        hdr = _make_header(1)
        hdr["hash"] = "abc"
        assert validate_header_dict(hdr) is False

    def test_validate_header_dict_non_string_hash(self):
        hdr = _make_header(1)
        hdr["hash"] = 12345
        assert validate_header_dict(hdr) is False


# ================================================================
# Blockchain validate_header_chain tests
# ================================================================

class TestValidateHeaderChain:
    def _bc(self):
        """Create a real Blockchain for header chain validation tests."""
        from qbit_network.core.blockchain import Blockchain
        bc = Blockchain()
        # Register a validator so header validation passes
        bc._validator_registry["qv1validator"] = b'\x00' * 1952
        return bc

    def test_empty_chain_valid(self):
        bc = self._bc()
        ok, err = bc.validate_header_chain([])
        assert ok is True

    def test_single_header_valid(self):
        bc = self._bc()
        hdr = _make_header(1, prev_hash="0" * 64)
        ok, err = bc.validate_header_chain([hdr], start_prev_hash="0" * 64)
        assert ok is True

    def test_valid_chain(self):
        bc = self._bc()
        chain = _make_header_chain(1, 5)
        ok, err = bc.validate_header_chain(chain, start_prev_hash="0" * 64)
        assert ok is True

    def test_broken_prev_hash_linkage(self):
        bc = self._bc()
        chain = _make_header_chain(1, 3)
        chain[1]["prevHash"] = "f" * 64  # break linkage
        ok, err = bc.validate_header_chain(chain, start_prev_hash="0" * 64)
        assert ok is False
        assert "broken prev_hash" in err

    def test_non_consecutive_indices(self):
        bc = self._bc()
        chain = _make_header_chain(1, 3)
        chain[2]["index"] = 10  # skip index
        ok, err = bc.validate_header_chain(chain, start_prev_hash="0" * 64)
        assert ok is False
        assert "non-consecutive" in err

    def test_non_monotonic_timestamp(self):
        bc = self._bc()
        chain = _make_header_chain(1, 3)
        chain[2]["timestamp"] = chain[1]["timestamp"] - 100
        ok, err = bc.validate_header_chain(chain, start_prev_hash="0" * 64)
        assert ok is False
        assert "non-monotonic timestamp" in err

    def test_unknown_validator(self):
        bc = self._bc()
        chain = _make_header_chain(1, 3, validator="qv1unknown")
        ok, err = bc.validate_header_chain(chain, start_prev_hash="0" * 64)
        assert ok is False
        assert "unknown validator" in err

    def test_start_prev_hash_mismatch(self):
        bc = self._bc()
        chain = _make_header_chain(1, 3, genesis_hash="a" * 64)
        ok, err = bc.validate_header_chain(chain, start_prev_hash="b" * 64)
        assert ok is False
        assert "doesn't connect" in err

    def test_missing_field(self):
        bc = self._bc()
        hdr = _make_header(1)
        del hdr["validator"]
        ok, err = bc.validate_header_chain([hdr])
        assert ok is False
        assert "missing field" in err

    def test_genesis_validator_skipped(self):
        """Genesis block (index 0) should skip validator registry check."""
        bc = self._bc()
        hdr = _make_header(0, validator="qv1anyone")
        ok, err = bc.validate_header_chain([hdr])
        assert ok is True


# ================================================================
# Start/stop lifecycle tests
# ================================================================

class TestSyncLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        await sm.start()
        assert sm._running is True
        assert sm._sync_task is not None
        await sm.stop()
        assert sm._running is False

    @pytest.mark.asyncio
    async def test_double_start_idempotent(self):
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        await sm.start()
        first_task = sm._sync_task
        await sm.start()  # should not create a new task
        assert sm._sync_task is first_task
        await sm.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_pending_futures(self):
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        sm._pending_responses["test"] = fut
        await sm.start()
        await sm.stop()
        assert fut.cancelled()
        assert len(sm._pending_responses) == 0


# ================================================================
# Cross-peer header verification tests
# ================================================================

class TestCrossPeerVerification:
    @pytest.mark.asyncio
    async def test_cross_verify_no_headers_returns_false(self):
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        result = await sm._cross_verify_headers()
        assert result is False

    @pytest.mark.asyncio
    async def test_cross_verify_not_enough_peers(self):
        """With only the sync source peer, cross-verification is skipped."""
        peer = _make_mock_peer("1.1.1.1:9000", height=100)
        p2p = _make_mock_p2p([peer])
        sm = SyncManager(_make_mock_blockchain(height=0), p2p)
        sm._sync_source_addr = "1.1.1.1:9000"
        sm._header_queue = _make_header_chain(1, 5)
        result = await sm._cross_verify_headers()
        assert result is False

    @pytest.mark.asyncio
    async def test_cross_verify_matching_headers_no_fork(self):
        """Cross-verification with matching hashes should return no fork."""
        headers = _make_header_chain(1, 200)
        sync_peer = _make_mock_peer("1.1.1.1:9000", height=200)
        verify_peer1 = _make_mock_peer("2.2.2.2:9000", height=200)
        verify_peer2 = _make_mock_peer("3.3.3.3:9000", height=200)
        p2p = _make_mock_p2p([sync_peer, verify_peer1, verify_peer2])
        bc = _make_mock_blockchain(height=0)
        sm = SyncManager(bc, p2p)
        sm._sync_source_addr = "1.1.1.1:9000"
        sm._header_queue = headers

        # Mock _request_headers to return matching headers at checkpoint heights
        checkpoint_map = {}
        for h in headers:
            if h["index"] % SYNC_CHECKPOINT_INTERVAL == 0 or h["index"] == headers[-1]["index"]:
                checkpoint_map[h["index"]] = [h]

        async def mock_request_headers(peer, from_height, count, request_id):
            return checkpoint_map.get(from_height, [])

        sm._request_headers = mock_request_headers
        result = await sm._cross_verify_headers()
        assert result is False

    @pytest.mark.asyncio
    async def test_cross_verify_detects_fork(self):
        """Cross-verification with mismatched hash should detect a fork."""
        headers = _make_header_chain(1, 200)
        sync_peer = _make_mock_peer("1.1.1.1:9000", height=200)
        verify_peer1 = _make_mock_peer("2.2.2.2:9000", height=200)
        verify_peer2 = _make_mock_peer("3.3.3.3:9000", height=200)
        p2p = _make_mock_p2p([sync_peer, verify_peer1, verify_peer2])
        bc = _make_mock_blockchain(height=0)
        sm = SyncManager(bc, p2p)
        sm._sync_source_addr = "1.1.1.1:9000"
        sm._header_queue = headers

        # Return different hash at the checkpoint height
        async def mock_request_headers(peer, from_height, count, request_id):
            h = _make_header(from_height, prev_hash="ff" * 32)
            h["hash"] = "deadbeef" * 8  # different hash
            return [h]

        sm._request_headers = mock_request_headers
        result = await sm._cross_verify_headers()
        assert result is True
        assert sm._fork_source_addr != ""
        assert sm._stats.get("forks_detected", 0) == 1

    @pytest.mark.asyncio
    async def test_cross_verify_timeout_continues(self):
        """Timeout from verification peer should not crash, just skip."""
        headers = _make_header_chain(1, 200)
        sync_peer = _make_mock_peer("1.1.1.1:9000", height=200)
        verify_peer1 = _make_mock_peer("2.2.2.2:9000", height=200)
        verify_peer2 = _make_mock_peer("3.3.3.3:9000", height=200)
        p2p = _make_mock_p2p([sync_peer, verify_peer1, verify_peer2])
        sm = SyncManager(_make_mock_blockchain(height=0), p2p)
        sm._sync_source_addr = "1.1.1.1:9000"
        sm._header_queue = headers

        async def mock_request_headers(peer, from_height, count, request_id):
            raise asyncio.TimeoutError()

        sm._request_headers = mock_request_headers
        result = await sm._cross_verify_headers()
        assert result is False  # no fork detected, just timeouts


# ================================================================
# Fork resolution tests
# ================================================================

class TestForkResolution:
    @pytest.mark.asyncio
    async def test_attempt_fork_resolution_empty(self):
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        await sm._attempt_fork_resolution([])
        # Should return without error

    @pytest.mark.asyncio
    async def test_attempt_fork_resolution_success(self):
        bc = _make_mock_blockchain(height=10)
        bc.try_reorg = MagicMock(return_value=(True, "reorg complete: 8 → 12"))
        sm = SyncManager(bc, _make_mock_p2p())

        # Create mock fork blocks
        fork_blocks = []
        for i in range(8, 13):
            fb = MagicMock()
            fb.index = i
            fork_blocks.append(fb)

        await sm._attempt_fork_resolution(fork_blocks)
        bc.try_reorg.assert_called_once()
        assert sm._stats.get("reorgs_applied", 0) == 1

    @pytest.mark.asyncio
    async def test_attempt_fork_resolution_rejected(self):
        bc = _make_mock_blockchain(height=10)
        bc.try_reorg = MagicMock(return_value=(False, "fork not longer"))
        sm = SyncManager(bc, _make_mock_p2p())

        fork_blocks = []
        for i in range(8, 11):
            fb = MagicMock()
            fb.index = i
            fork_blocks.append(fb)

        await sm._attempt_fork_resolution(fork_blocks)
        assert sm._stats.get("forks_rejected", 0) == 1

    @pytest.mark.asyncio
    async def test_deep_fork_rejected_without_try_reorg(self):
        """Forks deeper than MAX_REORG_DEPTH should be rejected without calling try_reorg."""
        bc = _make_mock_blockchain(height=100)
        bc.try_reorg = MagicMock()
        sm = SyncManager(bc, _make_mock_p2p())

        # Create a fork deeper than MAX_REORG_DEPTH (32)
        fork_blocks = []
        for i in range(10, 10 + MAX_REORG_DEPTH + 5):
            fb = MagicMock()
            fb.index = i
            fork_blocks.append(fb)

        await sm._attempt_fork_resolution(fork_blocks)
        bc.try_reorg.assert_not_called()
        assert sm._stats.get("forks_rejected", 0) == 1

    @pytest.mark.asyncio
    async def test_fork_blocks_sorted_by_index(self):
        """Fork blocks should be sorted by index before reorg attempt."""
        bc = _make_mock_blockchain(height=10)
        bc.try_reorg = MagicMock(return_value=(True, "ok"))
        sm = SyncManager(bc, _make_mock_p2p())

        # Pass blocks out of order
        fork_blocks = []
        for i in [10, 8, 9, 11]:
            fb = MagicMock()
            fb.index = i
            fork_blocks.append(fb)

        await sm._attempt_fork_resolution(fork_blocks)
        called_blocks = bc.try_reorg.call_args[0][0]
        indices = [b.index for b in called_blocks]
        assert indices == [8, 9, 10, 11]


# ================================================================
# PQC signature batch verification tests
# ================================================================

class TestSignatureVerification:
    @pytest.mark.asyncio
    async def test_verify_signature_no_executor(self):
        """Without sig_executor, verification should pass (skip)."""
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        sm._sig_executor = None
        block = MagicMock()
        block.index = 1
        assert await sm._verify_signature_async(block) is True

    @pytest.mark.asyncio
    async def test_verify_signature_unknown_validator(self):
        """Unknown validator should fail verification."""
        bc = _make_mock_blockchain()
        bc._validator_registry = {}
        sm = SyncManager(bc, _make_mock_p2p())
        sm._sig_executor = MagicMock()  # non-None executor

        block = MagicMock()
        block.index = 1
        block.validator = "qv1unknown"
        assert await sm._verify_signature_async(block) is False

    @pytest.mark.asyncio
    async def test_verify_signature_with_valid_key(self):
        """With a valid validator key, should call executor and return result."""
        bc = _make_mock_blockchain()
        bc._validator_registry = {"qv1test": b'\x00' * 1952}
        sm = SyncManager(bc, _make_mock_p2p())

        # Mock the executor
        mock_executor = MagicMock()
        sm._sig_executor = mock_executor

        block = MagicMock()
        block.index = 1
        block.validator = "qv1test"
        block.to_dict = MagicMock(return_value={"index": 1, "hash": "a" * 64})

        loop = asyncio.get_event_loop()
        # Patch run_in_executor to return True
        with patch.object(loop, 'run_in_executor', new_callable=AsyncMock, return_value=True):
            result = await sm._verify_signature_async(block)
        assert result is True


# ================================================================
# Header sync loop integration tests
# ================================================================

class TestHeaderSyncLoop:
    @pytest.mark.asyncio
    async def test_header_sync_downloads_and_transitions(self):
        """Header sync should download headers and transition to BLOCK_SYNC."""
        headers = _make_header_chain(1, 10)
        peer = _make_mock_peer("1.1.1.1:9000", height=10)
        p2p = _make_mock_p2p([peer])
        bc = _make_mock_blockchain(height=0)
        bc.latest_block = MagicMock()
        bc.latest_block.block_hash = "0" * 64

        sm = SyncManager(bc, p2p)
        sm._running = True
        sm._sync_source_addr = "1.1.1.1:9000"
        sm._target_height = 10
        sm._transition(SyncState.HEADER_SYNC)

        # Mock _request_headers to return all headers at once
        async def mock_request_headers(peer, from_height, count, request_id):
            return [h for h in headers if from_height <= h["index"] < from_height + count]

        sm._request_headers = mock_request_headers
        # Mock cross-verify to skip
        sm._cross_verify_headers = AsyncMock(return_value=False)

        await sm._header_sync()
        assert sm.state == SyncState.BLOCK_SYNC
        assert len(sm._header_queue) == 10
        assert sm._stats["headers_downloaded"] == 10

    @pytest.mark.asyncio
    async def test_header_sync_peer_disconnect_returns_idle(self):
        """If sync source disconnects, should return to IDLE."""
        peer = _make_mock_peer("1.1.1.1:9000", height=100, connected=False)
        p2p = _make_mock_p2p([peer])
        bc = _make_mock_blockchain(height=0)
        bc.latest_block = MagicMock()
        bc.latest_block.block_hash = "0" * 64

        sm = SyncManager(bc, p2p)
        sm._running = True
        sm._sync_source_addr = "1.1.1.1:9000"
        sm._target_height = 100
        sm._transition(SyncState.HEADER_SYNC)

        await sm._header_sync()
        assert sm.state == SyncState.IDLE

    @pytest.mark.asyncio
    async def test_header_sync_invalid_chain_bans_peer(self):
        """Invalid header chain should ban peer and return to IDLE."""
        peer = _make_mock_peer("1.1.1.1:9000", height=10)
        p2p = _make_mock_p2p([peer])
        bc = _make_mock_blockchain(height=0)
        bc.latest_block = MagicMock()
        bc.latest_block.block_hash = "0" * 64
        bc.validate_header_chain = MagicMock(return_value=(False, "broken chain"))

        sm = SyncManager(bc, p2p)
        sm._running = True
        sm._sync_source_addr = "1.1.1.1:9000"
        sm._target_height = 10
        sm._transition(SyncState.HEADER_SYNC)

        async def mock_request_headers(peer, from_height, count, request_id):
            return [_make_header(from_height)]

        sm._request_headers = mock_request_headers

        await sm._header_sync()
        assert sm.state == SyncState.IDLE
        p2p.reputation.record.assert_called_with("1.1.1.1:9000", "invalid_block")
        assert sm._stats["banned_peers"] == 1

    @pytest.mark.asyncio
    async def test_header_sync_timeout_retries_with_new_peer(self):
        """Timeout during header sync should retry with a different peer."""
        peer1 = _make_mock_peer("1.1.1.1:9000", height=10)
        peer2 = _make_mock_peer("2.2.2.2:9000", height=10)
        p2p = _make_mock_p2p([peer1, peer2])
        bc = _make_mock_blockchain(height=0)
        bc.latest_block = MagicMock()
        bc.latest_block.block_hash = "0" * 64

        sm = SyncManager(bc, p2p)
        sm._running = True
        sm._sync_source_addr = "1.1.1.1:9000"
        sm._target_height = 10
        sm._transition(SyncState.HEADER_SYNC)

        call_count = 0
        headers = _make_header_chain(1, 10)

        async def mock_request_headers(peer, from_height, count, request_id):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise asyncio.TimeoutError()
            return [h for h in headers if from_height <= h["index"] < from_height + count]

        sm._request_headers = mock_request_headers
        sm._cross_verify_headers = AsyncMock(return_value=False)

        await sm._header_sync()
        assert sm.state == SyncState.BLOCK_SYNC
        assert sm._stats["retries"] >= 1


# ================================================================
# Block sync loop integration tests
# ================================================================

class TestBlockSyncLoop:
    @pytest.mark.asyncio
    async def test_block_sync_empty_queue_transitions_to_synced(self):
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        sm._running = True
        sm._transition(SyncState.HEADER_SYNC)
        sm._transition(SyncState.BLOCK_SYNC)
        sm._header_queue = []
        sm._stats["sync_started_at"] = 1.0

        await sm._block_sync()
        assert sm.state == SyncState.SYNCED

    @pytest.mark.asyncio
    async def test_block_sync_downloads_and_validates(self):
        """Block sync should download blocks and add them to the blockchain."""
        headers = _make_header_chain(1, 5)
        peer = _make_mock_peer("1.1.1.1:9000", height=5)
        p2p = _make_mock_p2p([peer])
        bc = _make_mock_blockchain(height=0)

        sm = SyncManager(bc, p2p)
        sm._running = True
        sm._transition(SyncState.HEADER_SYNC)
        sm._transition(SyncState.BLOCK_SYNC)
        sm._header_queue = headers
        sm._stats["sync_started_at"] = 1.0

        # Mock _download_chunk to return block dicts
        async def mock_download_chunk(peer, heights, request_id):
            return [{"index": h, "hash": f"{h:064x}"} for h in heights]

        sm._download_chunk = mock_download_chunk

        # Mock Block.from_dict to return a mock block
        mock_block = MagicMock()
        mock_block.index = 1
        with patch('qbit_network.network.sync.Block', create=True) as MockBlock:
            MockBlock = MagicMock()
            with patch.dict('sys.modules', {}):
                # Simpler: just patch the import inside _block_sync
                import qbit_network.network.sync as sync_mod
                original_block_sync = sm._block_sync

                # Just run it and let add_block mock handle it
                await sm._block_sync()

        assert sm.state == SyncState.SYNCED
        assert sm._stats["blocks_downloaded"] > 0


# ================================================================
# Adversarial sync tests
# ================================================================

class TestAdversarialSync:
    @pytest.mark.asyncio
    async def test_invalid_header_chain_rejected(self):
        """Peer sending invalid header chain should be banned."""
        peer = _make_mock_peer("1.1.1.1:9000", height=50)
        p2p = _make_mock_p2p([peer])
        bc = _make_mock_blockchain(height=0)
        bc.latest_block = MagicMock()
        bc.latest_block.block_hash = "0" * 64
        bc.validate_header_chain = MagicMock(return_value=(False, "forged headers"))

        sm = SyncManager(bc, p2p)
        sm._running = True
        sm._sync_source_addr = "1.1.1.1:9000"
        sm._target_height = 50
        sm._transition(SyncState.HEADER_SYNC)

        async def mock_request_headers(peer, from_height, count, request_id):
            return [_make_header(from_height)]

        sm._request_headers = mock_request_headers
        await sm._header_sync()

        assert sm.state == SyncState.IDLE
        p2p.reputation.record.assert_called_with("1.1.1.1:9000", "invalid_block")

    @pytest.mark.asyncio
    async def test_slow_peer_timeout_and_retry(self):
        """Consistently slow peer should be abandoned after max retries."""
        peer = _make_mock_peer("1.1.1.1:9000", height=50)
        p2p = _make_mock_p2p([peer])
        bc = _make_mock_blockchain(height=0)
        bc.latest_block = MagicMock()
        bc.latest_block.block_hash = "0" * 64

        sm = SyncManager(bc, p2p)
        sm._running = True
        sm._sync_source_addr = "1.1.1.1:9000"
        sm._target_height = 50
        sm._transition(SyncState.HEADER_SYNC)

        async def mock_request_headers(peer, from_height, count, request_id):
            raise asyncio.TimeoutError()

        sm._request_headers = mock_request_headers
        await sm._header_sync()

        assert sm.state == SyncState.IDLE
        assert sm._stats["retries"] >= SYNC_MAX_RETRIES

    @pytest.mark.asyncio
    async def test_eclipse_attack_detected_via_cross_verify(self):
        """An eclipse attack (single peer providing fake chain) should be detected
        when cross-verification peers report different hashes."""
        headers = _make_header_chain(1, 200)
        malicious_peer = _make_mock_peer("evil.node:9000", height=200)
        honest_peer1 = _make_mock_peer("good1.node:9000", height=200)
        honest_peer2 = _make_mock_peer("good2.node:9000", height=200)
        p2p = _make_mock_p2p([malicious_peer, honest_peer1, honest_peer2])

        sm = SyncManager(_make_mock_blockchain(height=0), p2p)
        sm._sync_source_addr = "evil.node:9000"
        sm._header_queue = headers

        async def mock_request_headers(peer, from_height, count, request_id):
            # Honest peers return different hashes
            h = _make_header(from_height)
            h["hash"] = "honest" + "0" * 58  # different from malicious headers
            return [h]

        sm._request_headers = mock_request_headers
        fork_detected = await sm._cross_verify_headers()
        assert fork_detected is True

    @pytest.mark.asyncio
    async def test_deep_fork_during_sync_rejected(self):
        """A fork deeper than MAX_REORG_DEPTH should be rejected."""
        bc = _make_mock_blockchain(height=100)
        bc.try_reorg = MagicMock()
        sm = SyncManager(bc, _make_mock_p2p())

        # Create fork blocks starting from height 50 (depth=51 > MAX_REORG_DEPTH=32)
        fork_blocks = []
        for i in range(50, 110):
            fb = MagicMock()
            fb.index = i
            fork_blocks.append(fb)

        await sm._attempt_fork_resolution(fork_blocks)
        # try_reorg should NOT be called for deep forks
        bc.try_reorg.assert_not_called()
        assert sm._stats.get("forks_rejected", 0) == 1


# ================================================================
# _check_peers tests
# ================================================================

class TestCheckPeers:
    @pytest.mark.asyncio
    async def test_check_peers_transitions_to_header_sync(self):
        """When a peer is far ahead, should transition to HEADER_SYNC."""
        peer = _make_mock_peer("1.1.1.1:9000", height=100)
        p2p = _make_mock_p2p([peer])
        bc = _make_mock_blockchain(height=0)
        sm = SyncManager(bc, p2p)
        sm._running = True

        with patch('qbit_network.network.sync.asyncio.sleep', new_callable=AsyncMock):
            await sm._check_peers()

        assert sm.state == SyncState.HEADER_SYNC
        assert sm._sync_source_addr == "1.1.1.1:9000"
        assert sm._target_height == 100

    @pytest.mark.asyncio
    async def test_check_peers_already_synced(self):
        """When local height matches peers, should go directly to SYNCED."""
        peer = _make_mock_peer("1.1.1.1:9000", height=50)
        p2p = _make_mock_p2p([peer])
        bc = _make_mock_blockchain(height=50)
        sm = SyncManager(bc, p2p)
        sm._running = True

        with patch('qbit_network.network.sync.asyncio.sleep', new_callable=AsyncMock):
            await sm._check_peers()

        assert sm.state == SyncState.SYNCED

    @pytest.mark.asyncio
    async def test_check_peers_no_peers_stays_idle(self):
        """With no peers, should stay in IDLE."""
        p2p = _make_mock_p2p([])
        bc = _make_mock_blockchain(height=0)
        sm = SyncManager(bc, p2p)
        sm._running = True

        with patch('qbit_network.network.sync.asyncio.sleep', new_callable=AsyncMock):
            await sm._check_peers()

        assert sm.state == SyncState.IDLE

    @pytest.mark.asyncio
    async def test_check_peers_ahead_but_no_connected_peer(self):
        """Best height is ahead but no connected peer for sync source."""
        peer = _make_mock_peer("1.1.1.1:9000", height=100, connected=False)
        p2p = _make_mock_p2p([peer])
        # best_peer_height returns 100 even though peer is disconnected
        p2p.best_peer_height = MagicMock(return_value=100)
        bc = _make_mock_blockchain(height=0)
        sm = SyncManager(bc, p2p)
        sm._running = True

        with patch('qbit_network.network.sync.asyncio.sleep', new_callable=AsyncMock):
            await sm._check_peers()

        # No connected peer → stays IDLE (falls through to sleep)
        assert sm.state == SyncState.IDLE


# ================================================================
# _follow_tip tests
# ================================================================

class TestFollowTip:
    @pytest.mark.asyncio
    async def test_follow_tip_re_enters_sync(self):
        """If peers get ahead again, should re-enter HEADER_SYNC."""
        peer = _make_mock_peer("1.1.1.1:9000", height=200)
        p2p = _make_mock_p2p([peer])
        bc = _make_mock_blockchain(height=100)
        sm = SyncManager(bc, p2p)
        sm._running = True
        sm._transition(SyncState.HEADER_SYNC)
        sm._transition(SyncState.BLOCK_SYNC)
        sm._transition(SyncState.SYNCED)

        with patch('qbit_network.network.sync.asyncio.sleep', new_callable=AsyncMock):
            await sm._follow_tip()

        assert sm.state == SyncState.HEADER_SYNC
        assert sm._sync_source_addr == "1.1.1.1:9000"
        assert sm._target_height == 200

    @pytest.mark.asyncio
    async def test_follow_tip_stays_synced(self):
        """If still caught up, should stay SYNCED."""
        peer = _make_mock_peer("1.1.1.1:9000", height=100)
        p2p = _make_mock_p2p([peer])
        bc = _make_mock_blockchain(height=100)
        sm = SyncManager(bc, p2p)
        sm._running = True
        sm._transition(SyncState.HEADER_SYNC)
        sm._transition(SyncState.BLOCK_SYNC)
        sm._transition(SyncState.SYNCED)

        with patch('qbit_network.network.sync.asyncio.sleep', new_callable=AsyncMock):
            await sm._follow_tip()

        assert sm.state == SyncState.SYNCED

    @pytest.mark.asyncio
    async def test_follow_tip_no_peers_goes_idle(self):
        """If all peers disconnect, should go to IDLE."""
        p2p = _make_mock_p2p([])
        bc = _make_mock_blockchain(height=100)
        sm = SyncManager(bc, p2p)
        sm._running = True
        sm._transition(SyncState.HEADER_SYNC)
        sm._transition(SyncState.BLOCK_SYNC)
        sm._transition(SyncState.SYNCED)

        with patch('qbit_network.network.sync.asyncio.sleep', new_callable=AsyncMock):
            await sm._follow_tip()

        assert sm.state == SyncState.IDLE


# ================================================================
# on_chain_info_response tests
# ================================================================

class TestChainInfoResponse:
    def test_on_chain_info_response_resolves_future(self):
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        loop = asyncio.new_event_loop()
        fut = loop.create_future()
        request_id = "chain_info_123"
        sm._pending_responses[request_id] = fut

        data = {"height": 100, "best_hash": "a" * 64, "request_id": request_id}
        sm.on_chain_info_response(data)

        assert fut.done()
        assert fut.result() == data
        loop.close()

    def test_on_chain_info_response_unknown_id_ignored(self):
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        sm.on_chain_info_response({"height": 50, "request_id": "nonexistent"})
        # Should not raise

    def test_on_chain_info_response_already_done_future(self):
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        loop = asyncio.new_event_loop()
        fut = loop.create_future()
        fut.set_result({"already": "set"})
        sm._pending_responses["done_id"] = fut

        # Should not raise even though future is already done
        sm.on_chain_info_response({"request_id": "done_id", "height": 10})
        loop.close()


# ================================================================
# _main_loop tests
# ================================================================

class TestMainLoop:
    @pytest.mark.asyncio
    async def test_main_loop_dispatches_idle(self):
        """Main loop in IDLE state should call _check_peers."""
        bc = _make_mock_blockchain(height=0)
        p2p = _make_mock_p2p([])
        sm = SyncManager(bc, p2p)
        sm._running = True

        call_count = 0

        async def mock_check_peers():
            nonlocal call_count
            call_count += 1
            sm._running = False  # stop after one iteration

        sm._check_peers = mock_check_peers
        await sm._main_loop()
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_main_loop_dispatches_header_sync(self):
        """Main loop in HEADER_SYNC state should call _header_sync."""
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        sm._running = True
        sm._transition(SyncState.HEADER_SYNC)

        called = False

        async def mock_header_sync():
            nonlocal called
            called = True
            sm._running = False

        sm._header_sync = mock_header_sync
        await sm._main_loop()
        assert called is True

    @pytest.mark.asyncio
    async def test_main_loop_dispatches_block_sync(self):
        """Main loop in BLOCK_SYNC state should call _block_sync."""
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        sm._running = True
        sm._transition(SyncState.HEADER_SYNC)
        sm._transition(SyncState.BLOCK_SYNC)

        called = False

        async def mock_block_sync():
            nonlocal called
            called = True
            sm._running = False

        sm._block_sync = mock_block_sync
        await sm._main_loop()
        assert called is True

    @pytest.mark.asyncio
    async def test_main_loop_dispatches_follow_tip(self):
        """Main loop in SYNCED state should call _follow_tip."""
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        sm._running = True
        sm._transition(SyncState.HEADER_SYNC)
        sm._transition(SyncState.BLOCK_SYNC)
        sm._transition(SyncState.SYNCED)

        called = False

        async def mock_follow_tip():
            nonlocal called
            called = True
            sm._running = False

        sm._follow_tip = mock_follow_tip
        await sm._main_loop()
        assert called is True

    @pytest.mark.asyncio
    async def test_main_loop_handles_exception_gracefully(self):
        """Main loop should catch exceptions and continue."""
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        sm._running = True

        call_count = 0

        async def mock_check_peers():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("test error")
            sm._running = False

        sm._check_peers = mock_check_peers

        with patch('qbit_network.network.sync.asyncio.sleep', new_callable=AsyncMock):
            await sm._main_loop()

        assert call_count == 2

    @pytest.mark.asyncio
    async def test_main_loop_stops_on_cancel(self):
        """CancelledError should exit the main loop cleanly."""
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        sm._running = True

        async def mock_check_peers():
            raise asyncio.CancelledError()

        sm._check_peers = mock_check_peers
        await sm._main_loop()  # should not raise


# ================================================================
# _verify_block_signature standalone function tests
# ================================================================

class TestVerifyBlockSignatureStandalone:
    def test_verify_block_signature_invalid_data(self):
        """Invalid block data should return False."""
        from qbit_network.network.sync import _verify_block_signature
        result = _verify_block_signature({}, "00" * 976)
        assert result is False

    def test_verify_block_signature_bad_pk_hex(self):
        """Bad public key hex should return False."""
        from qbit_network.network.sync import _verify_block_signature
        result = _verify_block_signature({"index": 1}, "not_hex")
        assert result is False


# ================================================================
# Header PQC signature verification tests
# ================================================================

class TestHeaderSignatureVerification:
    def test_verify_header_signature_invalid_data(self):
        """Invalid header data should return False."""
        from qbit_network.network.sync import _verify_header_signature
        result = _verify_header_signature({}, "00" * 976)
        assert result is False

    def test_verify_header_signature_bad_pk_hex(self):
        """Bad public key hex should return False."""
        from qbit_network.network.sync import _verify_header_signature
        hdr = _make_header(1)
        result = _verify_header_signature(hdr, "not_hex")
        assert result is False

    def test_verify_header_signature_missing_signature(self):
        """Header without signature field should return False."""
        from qbit_network.network.sync import _verify_header_signature
        hdr = _make_header(1)
        del hdr["signature"]
        result = _verify_header_signature(hdr, "00" * 976)
        assert result is False

    @pytest.mark.asyncio
    async def test_verify_header_signatures_no_executor(self):
        """Without sig_executor, batch verification should pass (skip)."""
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        sm._sig_executor = None
        headers = _make_header_chain(1, 5)
        ok, err = await sm._verify_header_signatures_async(headers)
        assert ok is True
        assert err == ""

    @pytest.mark.asyncio
    async def test_verify_header_signatures_skips_genesis(self):
        """Genesis header (index 0) should be skipped during signature verification."""
        bc = _make_mock_blockchain()
        bc._validator_registry = {"qv1validator": b'\x00' * 1952}
        sm = SyncManager(bc, _make_mock_p2p())
        mock_executor = MagicMock()
        sm._sig_executor = mock_executor

        genesis = _make_header(0)
        loop = asyncio.get_event_loop()
        with patch.object(loop, 'run_in_executor', new_callable=AsyncMock, return_value=True):
            ok, err = await sm._verify_header_signatures_async([genesis])
        assert ok is True
        assert err == ""

    @pytest.mark.asyncio
    async def test_verify_header_signatures_unknown_validator(self):
        """Headers with unknown validator should fail immediately."""
        bc = _make_mock_blockchain()
        bc._validator_registry = {}  # empty registry
        sm = SyncManager(bc, _make_mock_p2p())
        sm._sig_executor = MagicMock()

        headers = [_make_header(1, validator="qv1unknown")]
        ok, err = await sm._verify_header_signatures_async(headers)
        assert ok is False
        assert "no public key" in err
        assert "qv1unknown" in err

    @pytest.mark.asyncio
    async def test_verify_header_signatures_missing_signature_field(self):
        """Header missing signature field should fail."""
        bc = _make_mock_blockchain()
        bc._validator_registry = {"qv1validator": b'\x00' * 1952}
        sm = SyncManager(bc, _make_mock_p2p())
        sm._sig_executor = MagicMock()

        hdr = _make_header(1)
        del hdr["signature"]
        ok, err = await sm._verify_header_signatures_async([hdr])
        assert ok is False
        assert "missing signature" in err

    @pytest.mark.asyncio
    async def test_verify_header_signatures_missing_validator_field(self):
        """Header with empty validator should fail."""
        bc = _make_mock_blockchain()
        sm = SyncManager(bc, _make_mock_p2p())
        sm._sig_executor = MagicMock()

        hdr = _make_header(1, validator="")
        ok, err = await sm._verify_header_signatures_async([hdr])
        assert ok is False
        assert "missing validator" in err

    @pytest.mark.asyncio
    async def test_verify_header_signatures_invalid_sig_rejects(self):
        """Invalid PQC signature should reject the header batch."""
        bc = _make_mock_blockchain()
        bc._validator_registry = {"qv1validator": b'\x00' * 1952}
        sm = SyncManager(bc, _make_mock_p2p())
        mock_executor = MagicMock()
        sm._sig_executor = mock_executor

        headers = _make_header_chain(1, 3)
        loop = asyncio.get_event_loop()
        # Simulate signature verification returning False (invalid sig)
        with patch.object(loop, 'run_in_executor', new_callable=AsyncMock, return_value=False):
            ok, err = await sm._verify_header_signatures_async(headers)
        assert ok is False
        assert "invalid PQC signature" in err

    @pytest.mark.asyncio
    async def test_verify_header_signatures_valid_batch(self):
        """Valid batch of headers should pass verification."""
        bc = _make_mock_blockchain()
        bc._validator_registry = {"qv1validator": b'\x00' * 1952}
        sm = SyncManager(bc, _make_mock_p2p())
        mock_executor = MagicMock()
        sm._sig_executor = mock_executor

        headers = _make_header_chain(1, 5)
        loop = asyncio.get_event_loop()
        with patch.object(loop, 'run_in_executor', new_callable=AsyncMock, return_value=True):
            ok, err = await sm._verify_header_signatures_async(headers)
        assert ok is True
        assert err == ""

    @pytest.mark.asyncio
    async def test_verify_header_signatures_executor_exception(self):
        """Executor exception should reject the header."""
        bc = _make_mock_blockchain()
        bc._validator_registry = {"qv1validator": b'\x00' * 1952}
        sm = SyncManager(bc, _make_mock_p2p())
        mock_executor = MagicMock()
        sm._sig_executor = mock_executor

        headers = [_make_header(1)]
        loop = asyncio.get_event_loop()
        with patch.object(loop, 'run_in_executor', new_callable=AsyncMock,
                          side_effect=RuntimeError("executor crashed")):
            ok, err = await sm._verify_header_signatures_async(headers)
        assert ok is False
        assert "verification error" in err


class TestHeaderSyncWithSignatureVerification:
    """Integration tests: _header_sync rejects forged headers with invalid PQC signatures."""

    @pytest.mark.asyncio
    async def test_header_sync_rejects_forged_signatures(self):
        """Headers with invalid PQC signatures should be rejected before block download."""
        headers = _make_header_chain(1, 5)
        peer = _make_mock_peer("1.1.1.1:9000", height=5)
        p2p = _make_mock_p2p([peer])
        bc = _make_mock_blockchain(height=0)
        bc.latest_block = MagicMock()
        bc.latest_block.block_hash = "0" * 64
        bc._validator_registry = {"qv1validator": b'\x00' * 1952}

        sm = SyncManager(bc, p2p)
        sm._running = True
        sm._sync_source_addr = "1.1.1.1:9000"
        sm._target_height = 5
        sm._transition(SyncState.HEADER_SYNC)
        sm._sig_executor = MagicMock()  # enable sig verification

        async def mock_request_headers(peer, from_height, count, request_id):
            return [h for h in headers if from_height <= h["index"] < from_height + count]
        sm._request_headers = mock_request_headers

        # Signature verification fails — forged headers
        loop = asyncio.get_event_loop()
        with patch.object(loop, 'run_in_executor', new_callable=AsyncMock, return_value=False):
            await sm._header_sync()

        # Should reject and go back to IDLE, not proceed to BLOCK_SYNC
        assert sm.state == SyncState.IDLE
        p2p.reputation.record.assert_called_with("1.1.1.1:9000", "invalid_block")
        assert sm._stats["banned_peers"] == 1
        assert len(sm._header_queue) == 0  # no forged headers accepted

    @pytest.mark.asyncio
    async def test_header_sync_accepts_valid_signatures(self):
        """Headers with valid PQC signatures should be accepted and sync continues."""
        headers = _make_header_chain(1, 5)
        peer = _make_mock_peer("1.1.1.1:9000", height=5)
        p2p = _make_mock_p2p([peer])
        bc = _make_mock_blockchain(height=0)
        bc.latest_block = MagicMock()
        bc.latest_block.block_hash = "0" * 64
        bc._validator_registry = {"qv1validator": b'\x00' * 1952}

        sm = SyncManager(bc, p2p)
        sm._running = True
        sm._sync_source_addr = "1.1.1.1:9000"
        sm._target_height = 5
        sm._transition(SyncState.HEADER_SYNC)
        sm._sig_executor = MagicMock()

        async def mock_request_headers(peer, from_height, count, request_id):
            return [h for h in headers if from_height <= h["index"] < from_height + count]
        sm._request_headers = mock_request_headers
        sm._cross_verify_headers = AsyncMock(return_value=False)

        # Signature verification passes
        loop = asyncio.get_event_loop()
        with patch.object(loop, 'run_in_executor', new_callable=AsyncMock, return_value=True):
            await sm._header_sync()

        assert sm.state == SyncState.BLOCK_SYNC
        assert len(sm._header_queue) == 5
        assert sm._stats["headers_downloaded"] == 5


# ================================================================
# _request_headers and _download_chunk tests
# ================================================================

class TestRequestAndDownload:
    @pytest.mark.asyncio
    async def test_request_headers_sends_message(self):
        """_request_headers should send MSG_GET_HEADERS to peer and return headers."""
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        peer = _make_mock_peer("1.1.1.1:9000", height=100)

        headers = [_make_header(1)]

        async def simulate_response():
            await asyncio.sleep(0.01)
            # Find the pending response and resolve it
            for rid, fut in list(sm._pending_responses.items()):
                if not fut.done():
                    fut.set_result({"headers": headers})

        task = asyncio.create_task(simulate_response())
        result = await sm._request_headers(peer, 1, 10, "req1")
        await task
        assert result == headers
        peer.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_request_headers_timeout(self):
        """_request_headers should raise TimeoutError on timeout."""
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        peer = _make_mock_peer("1.1.1.1:9000", height=100)

        with patch('qbit_network.network.sync.SYNC_TIMEOUT', 0.01):
            with pytest.raises(asyncio.TimeoutError):
                await sm._request_headers(peer, 1, 10, "req_timeout")

        # Future should be cleaned up
        assert "req_timeout" not in sm._pending_responses

    @pytest.mark.asyncio
    async def test_download_chunk_sends_message(self):
        """_download_chunk should send MSG_GET_BLOCK_BODIES and return blocks."""
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        peer = _make_mock_peer("1.1.1.1:9000", height=100)

        blocks = [{"index": 1, "hash": "a" * 64}]

        async def simulate_response():
            await asyncio.sleep(0.01)
            for rid, fut in list(sm._pending_responses.items()):
                if not fut.done():
                    fut.set_result({"blocks": blocks})

        task = asyncio.create_task(simulate_response())
        result = await sm._download_chunk(peer, [1], "chunk1")
        await task
        assert result == blocks

    @pytest.mark.asyncio
    async def test_download_chunk_timeout_returns_none(self):
        """_download_chunk should return None on timeout and record reputation."""
        p2p = _make_mock_p2p([])
        sm = SyncManager(_make_mock_blockchain(), p2p)
        peer = _make_mock_peer("1.1.1.1:9000", height=100)
        sm._sync_source_addr = "stub"  # not used in _download_chunk directly

        with patch('qbit_network.network.sync.SYNC_TIMEOUT', 0.01):
            result = await sm._download_chunk(peer, [1, 2, 3], "chunk_timeout")

        assert result is None
        p2p.reputation.record.assert_called_with("1.1.1.1:9000", "timeout")
        assert "chunk_timeout" not in sm._pending_responses

    def test_fork_state_tracking(self):
        """SyncManager should properly track fork-related state."""
        sm = SyncManager(_make_mock_blockchain(), _make_mock_p2p())
        assert sm._fork_headers is None
        assert sm._fork_source_addr == ""

    @pytest.mark.asyncio
    async def test_block_sync_clears_fork_state(self):
        """After block sync completes the full path, fork state should be cleared."""
        peer = _make_mock_peer("1.1.1.1:9000", height=5)
        p2p = _make_mock_p2p([peer])
        bc = _make_mock_blockchain(height=0)
        sm = SyncManager(bc, p2p)
        sm._running = True
        sm._transition(SyncState.HEADER_SYNC)
        sm._transition(SyncState.BLOCK_SYNC)
        sm._header_queue = _make_header_chain(1, 3)
        sm._fork_headers = [_make_header(1)]
        sm._fork_source_addr = "some.peer:9000"
        sm._stats["sync_started_at"] = 1.0

        async def mock_download_chunk(peer, heights, request_id):
            return [{"index": h, "hash": f"{h:064x}"} for h in heights]

        sm._download_chunk = mock_download_chunk

        await sm._block_sync()
        assert sm._fork_headers is None
        assert sm._fork_source_addr == ""
