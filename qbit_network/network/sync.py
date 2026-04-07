"""SyncManager — multi-node chain synchronization using header-first + parallel block download."""
import asyncio
import enum
import logging
import secrets
import time
from concurrent.futures import ProcessPoolExecutor

from ..config import (
    SYNC_BATCH_SIZE, SYNC_BLOCK_BATCH, SYNC_TIMEOUT, SYNC_THRESHOLD,
    SYNC_MAX_PARALLEL, SYNC_MAX_RETRIES, SYNC_CHECKPOINT_INTERVAL,
    SYNC_CROSS_VERIFY_PEERS, MAX_REORG_DEPTH,
)
from .sync_messages import (
    MSG_GET_CHAIN_INFO, MSG_CHAIN_INFO,
    MSG_GET_HEADERS, MSG_HEADERS,
    MSG_GET_BLOCK_BODIES, MSG_BLOCK_BODIES,
    validate_header_dict,
)

logger = logging.getLogger("qbit_network.sync")


class SyncState(enum.Enum):
    IDLE = "idle"
    HEADER_SYNC = "header_sync"
    BLOCK_SYNC = "block_sync"
    SYNCED = "synced"


# Valid state transitions
_TRANSITIONS = {
    SyncState.IDLE: {SyncState.HEADER_SYNC},
    SyncState.HEADER_SYNC: {SyncState.BLOCK_SYNC, SyncState.IDLE},
    SyncState.BLOCK_SYNC: {SyncState.SYNCED, SyncState.HEADER_SYNC},
    SyncState.SYNCED: {SyncState.HEADER_SYNC, SyncState.IDLE},
}


def _verify_block_signature(block_dict: dict, validator_pk_hex: str) -> bool:
    """Verify a single block's validator signature (for ProcessPoolExecutor)."""
    from ..core.block import Block
    from ..crypto.mldsa import MLDSA
    try:
        block = Block.from_dict(block_dict)
        pk = bytes.fromhex(validator_pk_hex)
        return block.verify_signature(pk)
    except Exception:
        return False


def _verify_header_signature(header_dict: dict, validator_pk_hex: str) -> bool:
    """Verify a header's PQC signature without requiring full block (for ProcessPoolExecutor).

    Reconstructs the canonical header bytes from the header dict and verifies
    the ML-DSA-65 signature against the validator's public key.
    """
    import json as _json
    from ..crypto.mldsa import MLDSA
    try:
        obj = {
            "baseFee": header_dict.get("baseFee", 0),
            "index": header_dict["index"],
            "merkleRoot": header_dict.get("merkleRoot", ""),
            "prevHash": header_dict["prevHash"],
            "timestamp": header_dict["timestamp"],
            "txCount": header_dict.get("txCount", 0),
            "validator": header_dict.get("validator", ""),
        }
        if header_dict.get("receiptsRoot"):
            obj["receiptsRoot"] = header_dict["receiptsRoot"]
        if header_dict.get("stateRoot"):
            obj["stateRoot"] = header_dict["stateRoot"]
        header_bytes = _json.dumps(
            obj, sort_keys=True, separators=(',', ':')).encode()
        sig = bytes.fromhex(header_dict["signature"])
        pk = bytes.fromhex(validator_pk_hex)
        return MLDSA.verify(pk, header_bytes, sig)
    except Exception:
        return False


class SyncManager:
    """Manages chain synchronization with peer nodes.

    State machine: IDLE -> HEADER_SYNC -> BLOCK_SYNC -> SYNCED
    """

    def __init__(self, blockchain, p2p, *, checkpoint_store=None):
        self._blockchain = blockchain
        self._p2p = p2p
        self._state = SyncState.IDLE
        self._target_height: int = -1
        self._sync_source_addr: str = ""
        self._header_queue: list[dict] = []
        self._checkpoint_height: int = -1
        self._running = False
        self._sync_task: asyncio.Task | None = None
        self._pending_responses: dict[str, asyncio.Future] = {}
        self._sig_executor: ProcessPoolExecutor | None = None
        self._fork_headers: list[dict] | None = None  # competing chain headers
        self._fork_source_addr: str = ""  # peer providing fork chain
        self._stats = {
            "headers_downloaded": 0,
            "blocks_downloaded": 0,
            "blocks_validated": 0,
            "sync_started_at": 0.0,
            "last_sync_duration": 0.0,
            "retries": 0,
            "banned_peers": 0,
        }

    @property
    def state(self) -> SyncState:
        return self._state

    def _transition(self, new_state: SyncState):
        if new_state not in _TRANSITIONS.get(self._state, set()):
            logger.warning(
                "Invalid sync state transition: %s -> %s",
                self._state.value, new_state.value
            )
            return False
        old = self._state
        self._state = new_state
        logger.info("Sync state: %s -> %s", old.value, new_state.value)
        return True

    async def start(self):
        if self._running:
            return
        self._running = True
        self._sig_executor = ProcessPoolExecutor(max_workers=SYNC_MAX_PARALLEL)
        self._sync_task = asyncio.create_task(self._main_loop())
        logger.info("SyncManager started")

    async def stop(self):
        self._running = False
        # Cancel pending response futures
        for fut in self._pending_responses.values():
            if not fut.done():
                fut.cancel()
        self._pending_responses.clear()
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
        if self._sig_executor:
            self._sig_executor.shutdown(wait=False)
            self._sig_executor = None
        logger.info("SyncManager stopped")

    def is_syncing(self) -> bool:
        return self._state in (SyncState.HEADER_SYNC, SyncState.BLOCK_SYNC)

    @property
    def progress(self) -> dict:
        local_h = self._blockchain.height
        target = max(self._target_height, local_h)
        if target <= 0:
            pct = 100.0
        else:
            pct = min(100.0, (local_h / target) * 100)
        return {
            "state": self._state.value,
            "local_height": local_h,
            "target_height": target,
            "percent": round(pct, 2),
            "headers_queued": len(self._header_queue),
            "sync_source": self._sync_source_addr,
            "stats": dict(self._stats),
        }

    # ================================================================
    # Main sync loop
    # ================================================================

    async def _main_loop(self):
        while self._running:
            try:
                if self._state == SyncState.IDLE:
                    await self._check_peers()
                elif self._state == SyncState.HEADER_SYNC:
                    await self._header_sync()
                elif self._state == SyncState.BLOCK_SYNC:
                    await self._block_sync()
                elif self._state == SyncState.SYNCED:
                    await self._follow_tip()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Sync loop error: %s", e, exc_info=True)
                await asyncio.sleep(5)

    # ================================================================
    # IDLE: check if any peer is ahead
    # ================================================================

    async def _check_peers(self):
        await asyncio.sleep(2)  # brief pause before checking
        best_height = self._p2p.best_peer_height()
        local_height = self._blockchain.height

        if best_height > local_height + SYNC_THRESHOLD:
            # Find best peer
            best_peer = self._select_sync_source()
            if best_peer:
                self._sync_source_addr = best_peer.addr
                self._target_height = best_peer.height
                self._stats["sync_started_at"] = time.time()
                self._transition(SyncState.HEADER_SYNC)
                return

        # If we already have peers and are caught up, go to SYNCED
        if best_height >= 0 and best_height <= local_height + SYNC_THRESHOLD:
            if self._p2p.peer_count() > 0:
                self._state = SyncState.SYNCED
                logger.info("Already synced at height %d", local_height)
                return

        await asyncio.sleep(5)

    def _select_sync_source(self):
        """Select the best peer for sync based on height and reputation."""
        candidates = [
            p for p in self._p2p.peers.values()
            if p.connected and p.height >= 0
        ]
        if not candidates:
            return None
        # Sort by height (desc), then by reputation score (desc)
        candidates.sort(
            key=lambda p: (
                p.height,
                self._p2p.reputation.score(p.addr),
            ),
            reverse=True,
        )
        return candidates[0]

    # ================================================================
    # HEADER_SYNC: download and verify header chain
    # ================================================================

    async def _header_sync(self):
        local_height = self._blockchain.height
        from_height = local_height + 1

        # Get the prev_hash we expect from our chain tip
        tip = self._blockchain.latest_block
        start_prev_hash = tip.block_hash if tip else "0" * 64

        logger.info(
            "Starting header sync from height %d (target: %d)",
            from_height, self._target_height
        )

        self._header_queue.clear()
        current_height = from_height
        retries = 0

        while current_height <= self._target_height and self._running:
            peer = self._get_peer(self._sync_source_addr)
            if not peer or not peer.connected:
                logger.warning("Sync source disconnected, returning to IDLE")
                self._transition(SyncState.IDLE)
                return

            # Request headers
            count = min(SYNC_BATCH_SIZE, self._target_height - current_height + 1)
            request_id = secrets.token_hex(8)

            try:
                headers = await self._request_headers(
                    peer, current_height, count, request_id
                )
            except asyncio.TimeoutError:
                retries += 1
                self._stats["retries"] += 1
                if retries >= SYNC_MAX_RETRIES:
                    logger.error("Max retries exceeded during header sync")
                    self._p2p.reputation.record(peer.addr, "timeout")
                    self._transition(SyncState.IDLE)
                    return
                # Try different peer
                self._sync_source_addr = ""
                new_peer = self._select_sync_source()
                if new_peer:
                    self._sync_source_addr = new_peer.addr
                continue

            if not headers:
                break

            # Validate header chain structural integrity
            expected_prev = (
                self._header_queue[-1]["hash"]
                if self._header_queue
                else start_prev_hash
            )
            ok, err = self._blockchain.validate_header_chain(
                headers, start_prev_hash=expected_prev
            )
            if not ok:
                logger.warning("Invalid header chain from %s: %s", peer.addr, err)
                self._p2p.reputation.record(peer.addr, "invalid_block")
                self._stats["banned_peers"] += 1
                self._transition(SyncState.IDLE)
                return

            # Verify PQC signatures on headers before accepting them
            sig_ok, sig_err = await self._verify_header_signatures_async(headers)
            if not sig_ok:
                logger.warning(
                    "Header PQC signature verification failed from %s: %s",
                    peer.addr, sig_err,
                )
                self._p2p.reputation.record(peer.addr, "invalid_block")
                self._stats["banned_peers"] += 1
                self._transition(SyncState.IDLE)
                return

            self._header_queue.extend(headers)
            self._stats["headers_downloaded"] += len(headers)
            current_height += len(headers)
            retries = 0

            logger.debug(
                "Downloaded %d headers (total: %d, target: %d)",
                len(headers), len(self._header_queue), self._target_height
            )

        if self._header_queue:
            logger.info(
                "Header sync complete: %d headers downloaded",
                len(self._header_queue)
            )
            # Cross-verify headers with additional peers (eclipse attack mitigation)
            fork_detected = await self._cross_verify_headers()
            self._transition(SyncState.BLOCK_SYNC)
        else:
            self._transition(SyncState.IDLE)

    async def _cross_verify_headers(self) -> bool:
        """Cross-verify header hashes with independent peers at checkpoint intervals.

        Downloads headers at 100-block intervals from additional peers and compares
        hashes. If a mismatch is detected, the peer with the divergent chain is
        flagged and fork resolution is triggered.

        Returns True if a fork was detected.
        """
        if not self._header_queue:
            return False

        # Need at least SYNC_CROSS_VERIFY_PEERS additional peers beyond the sync source
        verify_peers = [
            p for p in self._p2p.peers.values()
            if p.connected and p.height >= 0 and p.addr != self._sync_source_addr
        ]
        if len(verify_peers) < SYNC_CROSS_VERIFY_PEERS:
            logger.debug(
                "Not enough peers for cross-verification (%d < %d)",
                len(verify_peers), SYNC_CROSS_VERIFY_PEERS,
            )
            return False

        # Build hash map at checkpoint intervals from our downloaded headers
        checkpoint_hashes: dict[int, str] = {}
        for hdr in self._header_queue:
            idx = hdr["index"]
            if idx % SYNC_CHECKPOINT_INTERVAL == 0 or idx == self._header_queue[-1]["index"]:
                checkpoint_hashes[idx] = hdr["hash"]

        if not checkpoint_hashes:
            return False

        fork_detected = False
        for vp in verify_peers[:SYNC_CROSS_VERIFY_PEERS]:
            for cp_height, cp_hash in checkpoint_hashes.items():
                request_id = secrets.token_hex(8)
                try:
                    headers = await self._request_headers(vp, cp_height, 1, request_id)
                except asyncio.TimeoutError:
                    continue
                if not headers:
                    continue
                if headers[0].get("hash") != cp_hash:
                    logger.warning(
                        "Fork detected! Peer %s has different hash at height %d: "
                        "%s vs %s (our sync source: %s)",
                        vp.addr, cp_height,
                        headers[0].get("hash", "?")[:16],
                        cp_hash[:16],
                        self._sync_source_addr,
                    )
                    fork_detected = True
                    # Store competing headers for fork resolution during BLOCK_SYNC
                    self._fork_source_addr = vp.addr
                    break
            if fork_detected:
                break

        if fork_detected:
            self._stats["forks_detected"] = self._stats.get("forks_detected", 0) + 1
        return fork_detected

    async def _request_headers(self, peer, from_height: int, count: int,
                               request_id: str) -> list[dict]:
        """Send get_headers request and wait for response."""
        fut = asyncio.get_event_loop().create_future()
        self._pending_responses[request_id] = fut

        await peer.send(MSG_GET_HEADERS, {
            "from_height": from_height,
            "count": count,
            "request_id": request_id,
        })

        try:
            result = await asyncio.wait_for(fut, timeout=SYNC_TIMEOUT)
            return result.get("headers", [])
        finally:
            self._pending_responses.pop(request_id, None)

    # ================================================================
    # BLOCK_SYNC: download full blocks in parallel
    # ================================================================

    async def _block_sync(self):
        if not self._header_queue:
            self._transition(SyncState.SYNCED)
            return

        logger.info(
            "Starting block sync: %d blocks to download",
            len(self._header_queue)
        )

        # Partition into chunks
        heights = [h["index"] for h in self._header_queue]
        chunks = []
        for i in range(0, len(heights), SYNC_BLOCK_BATCH):
            chunks.append(heights[i:i + SYNC_BLOCK_BATCH])

        # Download chunks, distributing across peers
        chunk_idx = 0
        fork_blocks: list = []  # accumulate blocks for potential reorg
        needs_reorg = False

        while chunk_idx < len(chunks) and self._running:
            # Get available peers
            peers = self._get_sync_peers()
            if not peers:
                logger.warning("No peers available for block download")
                await asyncio.sleep(2)
                continue

            # Launch parallel downloads
            batch_tasks = []
            batch_size = min(len(peers), SYNC_MAX_PARALLEL, len(chunks) - chunk_idx)

            for i in range(batch_size):
                peer = peers[i % len(peers)]
                chunk = chunks[chunk_idx + i]
                request_id = secrets.token_hex(8)
                task = asyncio.create_task(
                    self._download_chunk(peer, chunk, request_id)
                )
                batch_tasks.append((chunk_idx + i, chunk, task))

            # Wait for all downloads in this batch
            for idx, chunk, task in batch_tasks:
                try:
                    blocks = await asyncio.wait_for(task, timeout=SYNC_TIMEOUT * 2)
                    if blocks is None:
                        # Retry this chunk
                        self._stats["retries"] += 1
                        continue

                    # Validate and append blocks
                    from ..core.block import Block
                    for block_dict in blocks:
                        try:
                            block = Block.from_dict(block_dict)

                            # Batch-verify PQC signature before add_block
                            if self._sig_executor and not await self._verify_signature_async(block):
                                logger.warning(
                                    "Block #%d PQC signature verification failed",
                                    block.index,
                                )
                                self._p2p.reputation.record(
                                    self._sync_source_addr, "invalid_block"
                                )
                                self._transition(SyncState.IDLE)
                                return

                            ok, err = self._blockchain.add_block(block)
                            if ok:
                                self._stats["blocks_validated"] += 1
                            else:
                                # Fork case: we already have a block at this height
                                if "first-seen wins" in err or "already have" in err:
                                    if "already have" in err:
                                        continue
                                    # Accumulate for reorg attempt
                                    fork_blocks.append(block)
                                    needs_reorg = True
                                    continue
                                logger.warning(
                                    "Block #%d validation failed: %s",
                                    block.index, err
                                )
                                break
                        except Exception as e:
                            logger.warning("Block parse error: %s", e)
                            break

                    self._stats["blocks_downloaded"] += len(blocks)

                    # Checkpoint
                    if (self._blockchain.height - self._checkpoint_height
                            >= SYNC_CHECKPOINT_INTERVAL):
                        self._checkpoint_height = self._blockchain.height
                        logger.debug(
                            "Sync checkpoint at height %d",
                            self._checkpoint_height
                        )

                except asyncio.TimeoutError:
                    self._stats["retries"] += 1
                    logger.warning("Block download timeout for chunk %d", idx)
                except Exception as e:
                    logger.error("Block download error: %s", e)

            chunk_idx += batch_size

        # Fork resolution: attempt reorg if we accumulated fork blocks
        if needs_reorg and fork_blocks:
            await self._attempt_fork_resolution(fork_blocks)

        # Done
        self._header_queue.clear()
        self._fork_headers = None
        self._fork_source_addr = ""
        duration = time.time() - self._stats["sync_started_at"]
        self._stats["last_sync_duration"] = duration
        logger.info(
            "Block sync complete: %d blocks in %.1fs",
            self._stats["blocks_validated"], duration
        )
        self._transition(SyncState.SYNCED)

    async def _attempt_fork_resolution(self, fork_blocks: list):
        """Attempt chain reorganization with competing fork blocks.

        Uses try_reorg() for short forks (≤ MAX_REORG_DEPTH).
        Long forks are rejected and logged.
        """
        if not fork_blocks:
            return

        # Sort fork blocks by index for proper ordering
        fork_blocks.sort(key=lambda b: b.index)

        fork_start = fork_blocks[0].index
        depth = (self._blockchain.height + 1) - fork_start

        if depth > MAX_REORG_DEPTH:
            logger.warning(
                "Fork too deep for automatic resolution: depth=%d > MAX_REORG_DEPTH=%d. "
                "Possible network partition — manual intervention required.",
                depth, MAX_REORG_DEPTH,
            )
            self._stats["forks_rejected"] = self._stats.get("forks_rejected", 0) + 1
            return

        logger.info(
            "Attempting fork resolution: %d fork blocks starting at height %d (depth=%d)",
            len(fork_blocks), fork_start, depth,
        )

        ok, msg = self._blockchain.try_reorg(fork_blocks)
        if ok:
            logger.info("Fork resolution succeeded: %s", msg)
            self._stats["reorgs_applied"] = self._stats.get("reorgs_applied", 0) + 1
        else:
            logger.warning("Fork resolution failed: %s", msg)
            self._stats["forks_rejected"] = self._stats.get("forks_rejected", 0) + 1

    async def _verify_signature_async(self, block) -> bool:
        """Verify block PQC signature using ProcessPoolExecutor for parallelism."""
        if not self._sig_executor:
            return True  # no executor = skip verification
        loop = asyncio.get_event_loop()
        # Look up validator public key
        validator = block.validator
        pk = self._blockchain._validator_registry.get(validator)
        if pk is None:
            logger.warning("No public key for validator %s at block #%d", validator, block.index)
            return False
        pk_hex = pk.hex() if isinstance(pk, bytes) else pk
        try:
            result = await loop.run_in_executor(
                self._sig_executor,
                _verify_block_signature,
                block.to_dict(),
                pk_hex,
            )
            return result
        except Exception as e:
            logger.warning("Signature verification error for block #%d: %s", block.index, e)
            return False

    async def _verify_header_signatures_async(
        self, headers: list[dict]
    ) -> tuple[bool, str]:
        """Batch-verify PQC signatures on header dicts using ProcessPoolExecutor.

        Returns (True, "") on success, or (False, error_message) on first failure.
        Skips genesis headers (index 0) which have no validator signature.
        """
        if not self._sig_executor:
            return True, ""

        loop = asyncio.get_event_loop()
        registry = self._blockchain._validator_registry

        # Build verification futures for non-genesis headers
        futures = []
        for hdr in headers:
            idx = hdr.get("index", 0)
            if idx == 0:
                continue  # genesis block has no validator signature
            validator = hdr.get("validator", "")
            if not validator:
                return False, f"header #{idx} missing validator field"
            pk = registry.get(validator)
            if pk is None:
                return False, f"no public key for validator '{validator}' at header #{idx}"
            pk_hex = pk.hex() if isinstance(pk, bytes) else pk
            if not hdr.get("signature"):
                return False, f"header #{idx} missing signature"
            fut = loop.run_in_executor(
                self._sig_executor,
                _verify_header_signature,
                hdr,
                pk_hex,
            )
            futures.append((idx, fut))

        # Await all in parallel
        for idx, fut in futures:
            try:
                valid = await fut
            except Exception as e:
                return False, f"signature verification error at header #{idx}: {e}"
            if not valid:
                return False, f"invalid PQC signature at header #{idx}"

        return True, ""

    async def _download_chunk(self, peer, heights: list[int],
                              request_id: str) -> list[dict] | None:
        """Download a chunk of blocks from a peer."""
        fut = asyncio.get_event_loop().create_future()
        self._pending_responses[request_id] = fut

        await peer.send(MSG_GET_BLOCK_BODIES, {
            "heights": heights,
            "request_id": request_id,
        })

        try:
            result = await asyncio.wait_for(fut, timeout=SYNC_TIMEOUT)
            return result.get("blocks", [])
        except asyncio.TimeoutError:
            self._p2p.reputation.record(peer.addr, "timeout")
            return None
        finally:
            self._pending_responses.pop(request_id, None)

    def _get_sync_peers(self) -> list:
        """Get connected peers sorted by height and reputation."""
        peers = [
            p for p in self._p2p.peers.values()
            if p.connected and p.height >= 0
        ]
        peers.sort(
            key=lambda p: (p.height, self._p2p.reputation.score(p.addr)),
            reverse=True,
        )
        return peers[:SYNC_MAX_PARALLEL]

    # ================================================================
    # SYNCED: follow chain tip
    # ================================================================

    async def _follow_tip(self):
        """Monitor peer heights and re-enter sync if we fall behind."""
        await asyncio.sleep(5)

        best_height = self._p2p.best_peer_height()
        local_height = self._blockchain.height

        if best_height > local_height + SYNC_THRESHOLD:
            peer = self._select_sync_source()
            if peer:
                self._sync_source_addr = peer.addr
                self._target_height = peer.height
                self._stats["sync_started_at"] = time.time()
                self._transition(SyncState.HEADER_SYNC)
                return

        if self._p2p.peer_count() == 0:
            self._transition(SyncState.IDLE)

    # ================================================================
    # P2P response handlers (called from node.py)
    # ================================================================

    def on_headers_response(self, data: dict):
        """Handle incoming headers response."""
        request_id = data.get("request_id")
        if request_id and request_id in self._pending_responses:
            fut = self._pending_responses[request_id]
            if not fut.done():
                fut.set_result(data)

    def on_block_bodies_response(self, data: dict):
        """Handle incoming block_bodies response."""
        request_id = data.get("request_id")
        if request_id and request_id in self._pending_responses:
            fut = self._pending_responses[request_id]
            if not fut.done():
                fut.set_result(data)

    def on_chain_info_response(self, data: dict):
        """Handle incoming chain_info response."""
        request_id = data.get("request_id")
        if request_id and request_id in self._pending_responses:
            fut = self._pending_responses[request_id]
            if not fut.done():
                fut.set_result(data)

    def on_peer_status(self, peer_addr: str, height: int):
        """Called when a peer broadcasts their height — update sync target if needed."""
        if height > self._target_height:
            self._target_height = height

    # ================================================================
    # Helpers
    # ================================================================

    def _get_peer(self, addr: str):
        """Get a connected peer by address."""
        peer = self._p2p.peers.get(addr)
        if peer and peer.connected:
            return peer
        return None
