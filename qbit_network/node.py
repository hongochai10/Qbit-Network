"""QVault full node - ties blockchain, P2P, and RPC together."""
import asyncio
import collections
import logging
import os
import secrets
import time
from .core.wallet import Wallet
from .core.blockchain import Blockchain
from .core.transaction import Transaction, TxType
from .core.block import Block
from .crypto import MLKEM
from .network.p2p import (
    P2PNode, MSG_NEW_BLOCK, MSG_NEW_TX,
    MSG_GET_BLOCKS, MSG_BLOCKS, MSG_GET_PEERS, MSG_PEERS,
    MSG_STATUS, _is_safe_peer,
)
from .network.rpc import RPCServer
from .config import DEFAULT_P2P_PORT, DEFAULT_RPC_PORT, BLOCK_INTERVAL

logger = logging.getLogger("qbit_network.node")

_MAX_SHARED_SECRETS = 10000


class FullNode:
    """QVault full node."""

    def __init__(self, *, host: str = "0.0.0.0", p2p_port: int = DEFAULT_P2P_PORT,
                 rpc_port: int = DEFAULT_RPC_PORT, data_dir: str = "",
                 bootstrap: list[str] | None = None, rpc_token: str = "",
                 tls_cert: str = "", tls_key: str = "",
                 tls_self_signed: bool = False):
        self.data_dir = data_dir
        self.blockchain = Blockchain(data_dir=data_dir)
        self.wallets: dict[str, Wallet] = {}
        self.validator_wallet: Wallet | None = None

        self.p2p = P2PNode(host, p2p_port, node_id=secrets.token_hex(16))
        self.rpc = RPCServer(host, rpc_port, auth_token=rpc_token,
                             tls_cert=tls_cert, tls_key=tls_key,
                             tls_self_signed=tls_self_signed, data_dir=data_dir)
        self.bootstrap = bootstrap or []
        self._running = False
        self._block_task = None
        self._sync_task = None
        self._shared_secrets: collections.OrderedDict[str, bytes] = collections.OrderedDict()
        self._wallet_locks: dict[str, asyncio.Lock] = {}  # per-address tx submission lock

    def _store_shared_secret(self, tx_id: str, ss: bytes):
        self._shared_secrets[tx_id] = ss
        while len(self._shared_secrets) > _MAX_SHARED_SECRETS:
            self._shared_secrets.popitem(last=False)

    # ================================================================
    # Wallet persistence
    # ================================================================

    def _wallets_dir(self) -> str:
        base = self.data_dir or os.path.expanduser("~/.qbit")
        return os.path.join(base, "wallets")

    def _save_wallets(self):
        d = self._wallets_dir()
        os.makedirs(d, exist_ok=True)
        for addr, w in self.wallets.items():
            path = os.path.join(d, f"{addr}.json")
            if not os.path.exists(path):
                w.save(path)

    def _load_wallets(self):
        d = self._wallets_dir()
        if not os.path.isdir(d):
            return
        for fname in os.listdir(d):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(d, fname)
            try:
                w = Wallet.load(path)
                self.wallets[w.address] = w
            except Exception as e:
                logger.warning(f"Failed to load wallet {fname}: {e}")

    # ================================================================
    # P2P handlers
    # ================================================================

    def _register_p2p(self):
        self.p2p.on(MSG_NEW_BLOCK, self._p2p_new_block)
        self.p2p.on(MSG_NEW_TX, self._p2p_new_tx)
        self.p2p.on(MSG_GET_BLOCKS, self._p2p_get_blocks)
        self.p2p.on(MSG_BLOCKS, self._p2p_blocks)
        self.p2p.on(MSG_GET_PEERS, self._p2p_get_peers)
        self.p2p.on(MSG_PEERS, self._p2p_peers)
        self.p2p.on(MSG_STATUS, self._p2p_status)

    async def _p2p_new_block(self, peer, data):
        try:
            block = Block.from_dict(data["block"])
            ok, err = self.blockchain.add_block(block)
            if ok:
                self._lock_genesis_if_needed()
                await self.p2p.broadcast(
                    MSG_NEW_BLOCK, {"block": block.to_dict()}, exclude=peer.addr)
        except Exception as e:
            logger.debug(f"bad block from {peer.addr}: {e}")

    async def _p2p_new_tx(self, peer, data):
        try:
            tx = Transaction.from_dict(data["tx"])
            ok, _ = self.blockchain.submit_tx(tx)
            if ok:
                await self.p2p.broadcast(
                    MSG_NEW_TX, {"tx": tx.to_dict()}, exclude=peer.addr)
        except Exception:
            pass

    async def _p2p_get_blocks(self, peer, data):
        try:
            start = max(0, int(data.get("from", 0)))
            count = min(int(data.get("count", 50)), 100)
        except (TypeError, ValueError):
            return
        chain = self.blockchain.chain
        blocks = []
        for i in range(start, min(start + count, len(chain))):
            blocks.append(chain[i].to_dict())
        # Echo back request_id for correlation (ISS-005)
        resp = {"blocks": blocks}
        req_id = data.get("request_id")
        if req_id:
            resp["request_id"] = req_id
        await peer.send(MSG_BLOCKS, resp)

    async def _p2p_blocks(self, peer, data):
        # Reject unsolicited blocks — require matching request_id (ISS-005)
        req_id = data.get("request_id")
        if req_id:
            if req_id not in self.p2p._pending_requests:
                logger.debug(f"Ignoring blocks with unknown request_id from {peer.addr}")
                return
            self.p2p._pending_requests.pop(req_id, None)
        else:
            # No request_id = unsolicited — drop
            logger.debug(f"Dropping unsolicited MSG_BLOCKS from {peer.addr}")
            return

        blocks_list = data.get("blocks", [])
        if not isinstance(blocks_list, list):
            return
        for bd in blocks_list[:100]:
            try:
                block = Block.from_dict(bd)
                ok, _ = self.blockchain.add_block(block)
                if ok:
                    self._lock_genesis_if_needed()
                else:
                    break
            except Exception:
                break

    async def _p2p_get_peers(self, peer, data):
        addrs = [a for a, p in self.p2p.peers.items()
                 if p.connected and not a.startswith("_")]
        await peer.send(MSG_PEERS, {"peers": addrs})

    async def _p2p_peers(self, peer, data):
        peers_list = data.get("peers", [])
        if not isinstance(peers_list, list):
            return
        for addr in peers_list[:50]:  # cap to prevent connection flood
            parts = addr.split(":")
            if len(parts) == 2:
                try:
                    port = int(parts[1])
                except ValueError:
                    continue
                if _is_safe_peer(parts[0], port, self.p2p.host, self.p2p.port):
                    await self.p2p.connect(parts[0], port)

    async def _p2p_status(self, peer, data):
        h = data.get("height", -1)
        if isinstance(h, int) and -1 <= h <= 10_000_000:
            peer.height = h

    # ================================================================
    # Chain sync
    # ================================================================

    async def _sync_loop(self):
        """Periodic chain sync: broadcast height, request missing blocks."""
        while self._running:
            await asyncio.sleep(BLOCK_INTERVAL * 2)
            if not self._running:
                break
            try:
                # Prune stale pending requests (TTL 60s)
                now = time.time()
                stale = [rid for rid, ts in self.p2p._pending_requests.items()
                         if now - ts > 60]
                for rid in stale:
                    self.p2p._pending_requests.pop(rid, None)

                my_height = self.blockchain.height
                await self.p2p.broadcast(MSG_STATUS, {"height": my_height})
                # Check if any peer is ahead
                best = self.p2p.best_peer_height()
                if best > my_height:
                    # Send to best peer only, not broadcast (avoid N-fold amplification)
                    best_peer = max(
                        (p for p in self.p2p.peers.values() if p.connected and p.height >= 0),
                        key=lambda p: p.height, default=None)
                    if best_peer:
                        req_id = secrets.token_hex(8)
                        self.p2p._pending_requests[req_id] = time.time()
                        await best_peer.send(MSG_GET_BLOCKS, {
                            "from": my_height + 1,
                            "count": min(best - my_height, 100),
                            "request_id": req_id,
                        })
            except Exception as e:
                logger.debug(f"Sync error: {e}")

    # ================================================================
    # RPC methods
    # ================================================================

    def _register_rpc(self):
        m = self.rpc.method

        # Chain (public)
        m("qv_blockNumber", self._rpc_block_number)
        m("qv_getBlock", self._rpc_get_block)
        m("qv_getTransaction", self._rpc_get_tx)
        m("qv_pendingTxCount", self._rpc_pending_count)
        m("qv_verifyDocument", self._rpc_verify_document)
        m("qv_getEncryptionPk", self._rpc_get_encryption_pk)
        m("qv_peerCount", self._rpc_peer_count)
        m("qv_nodeInfo", self._rpc_node_info)
        m("qv_validators", self._rpc_validators)
        m("qv_getTxsBySender", self._rpc_txs_by_sender)
        m("qv_getTxsByRecipient", self._rpc_txs_by_recipient)

        # Protected (require auth token)
        m("qv_notarize", self._rpc_notarize)
        m("qv_store", self._rpc_store)
        m("qv_share", self._rpc_share)
        m("qv_registerKey", self._rpc_register_key)
        m("qv_getSharedWithMe", self._rpc_shared_with_me)
        m("qv_getSharedSecret", self._rpc_get_shared_secret)
        m("qv_decapsulateShared", self._rpc_decapsulate_shared)
        m("qv_sendRawTransaction", self._rpc_send_raw_tx)
        m("qv_newWallet", self._rpc_new_wallet)
        m("qv_listWallets", self._rpc_list_wallets)
        m("qv_getWalletKeys", self._rpc_get_wallet_keys)

    async def _rpc_block_number(self):
        return self.blockchain.height

    async def _rpc_get_block(self, index=None, **kwargs):
        block_hash = kwargs.get("block_hash")
        if index is not None:
            if not isinstance(index, int):
                raise ValueError("index must be integer")
            key = index
        elif block_hash is not None:
            if not isinstance(block_hash, str):
                raise ValueError("block_hash must be string")
            key = block_hash
        else:
            return None
        block = self.blockchain.get_block(key)
        return block.to_dict() if block else None

    async def _rpc_get_tx(self, tx_id=""):
        if not isinstance(tx_id, str):
            raise ValueError("tx_id must be string")
        tx = self.blockchain.get_tx(tx_id)
        if not tx:
            return None
        block_idx = self.blockchain.get_tx_block(tx_id)
        result = tx.to_dict()
        result["block_index"] = block_idx
        return result

    async def _rpc_pending_count(self):
        return len(self.blockchain.tx_pool)

    async def _rpc_verify_document(self, document_hash=""):
        if not isinstance(document_hash, str):
            raise ValueError("document_hash must be string")
        return self.blockchain.verify_document(document_hash)

    async def _rpc_get_encryption_pk(self, address=""):
        if not isinstance(address, str):
            raise ValueError("address must be string")
        pk = self.blockchain.get_encryption_pk(address)
        if not pk:
            raise ValueError(f"no encryption key registered for {address[:16]}...")
        return {"address": address, "encryption_pk": pk}

    async def _rpc_peer_count(self):
        return self.p2p.peer_count()

    async def _rpc_node_info(self):
        return {
            "version": "0.1.0",
            "chain_height": self.blockchain.height,
            "pending_txs": len(self.blockchain.tx_pool),
            "peers": self.p2p.peer_count(),
            "validator": self.validator_wallet.address if self.validator_wallet else None,
            "validators": sorted(self.blockchain.consensus.validators.keys()),
            "wallets": len(self.wallets),
        }

    async def _rpc_validators(self):
        return sorted(self.blockchain.consensus.validators.keys())

    async def _rpc_txs_by_sender(self, address=""):
        if not isinstance(address, str):
            raise ValueError("address must be string")
        return self.blockchain.get_txs_by_sender(address)

    async def _rpc_txs_by_recipient(self, address=""):
        if not isinstance(address, str):
            raise ValueError("address must be string")
        return self.blockchain.get_txs_by_recipient(address)

    # ---- Protected ----

    def _next_nonce(self, address: str) -> int:
        base = self.blockchain.get_nonce(address)
        pending = self.blockchain._pool_sender_count.get(address, 0)
        return base + pending

    async def _rpc_register_key(self, wallet_address=""):
        w = self._get_wallet(wallet_address)
        async with self._lock_for(w.address):
            tx = Transaction.register_key(
                w.address, w.encryption_pk, nonce=self._next_nonce(w.address))
            tx.sign(w.signing_sk, w.signing_pk)
            ok, result = self.blockchain.submit_tx(tx)
        if not ok:
            raise ValueError(result)
        await self.p2p.broadcast(MSG_NEW_TX, {"tx": tx.to_dict()})
        return {"tx_id": result}

    async def _rpc_notarize(self, wallet_address="", document_hash="", metadata=""):
        w = self._get_wallet(wallet_address)
        async with self._lock_for(w.address):
            tx = Transaction.notarize(
                w.address, document_hash, metadata, nonce=self._next_nonce(w.address))
            tx.sign(w.signing_sk, w.signing_pk)
            ok, result = self.blockchain.submit_tx(tx)
        if not ok:
            raise ValueError(result)
        await self.p2p.broadcast(MSG_NEW_TX, {"tx": tx.to_dict()})
        return {"tx_id": result}

    async def _rpc_store(self, wallet_address="", document_hash="", cid="", metadata=""):
        w = self._get_wallet(wallet_address)
        async with self._lock_for(w.address):
            tx = Transaction.store(
                w.address, document_hash, cid, metadata, nonce=self._next_nonce(w.address))
            tx.sign(w.signing_sk, w.signing_pk)
            ok, result = self.blockchain.submit_tx(tx)
        if not ok:
            raise ValueError(result)
        await self.p2p.broadcast(MSG_NEW_TX, {"tx": tx.to_dict()})
        return {"tx_id": result}

    async def _rpc_share(self, wallet_address="", recipient_address="",
                         cid="", recipient_encryption_pk="", expires=0):
        w = self._get_wallet(wallet_address)
        if not recipient_encryption_pk:
            pk_hex = self.blockchain.get_encryption_pk(recipient_address)
            if not pk_hex:
                raise ValueError(
                    f"no encryption key for {recipient_address[:16]}... "
                    f"(register with qv_registerKey first)")
            recipient_encryption_pk = pk_hex

        pk_bytes = bytes.fromhex(recipient_encryption_pk)
        ciphertext, shared_secret = MLKEM.encapsulate(pk_bytes)

        async with self._lock_for(w.address):
            tx = Transaction.share(
                w.address, recipient_address, cid, ciphertext, expires,
                nonce=self._next_nonce(w.address))
            tx.sign(w.signing_sk, w.signing_pk)
            ok, result = self.blockchain.submit_tx(tx)
        if not ok:
            raise ValueError(result)
        await self.p2p.broadcast(MSG_NEW_TX, {"tx": tx.to_dict()})

        self._store_shared_secret(result, shared_secret)
        return {"tx_id": result, "shared_secret_stored": True}

    async def _rpc_shared_with_me(self, address=""):
        if not isinstance(address, str):
            raise ValueError("address must be string")
        return self.blockchain.get_shared_with(address)

    async def _rpc_get_shared_secret(self, tx_id=""):
        ss = self._shared_secrets.get(tx_id)
        if not ss:
            raise ValueError("shared secret not found (only available to sender on this node)")
        return {"shared_secret": ss.hex()}

    async def _rpc_decapsulate_shared(self, wallet_address="", tx_id=""):
        if not isinstance(tx_id, str):
            raise ValueError("tx_id must be string")
        w = self._get_wallet(wallet_address)
        tx = self.blockchain.get_tx(tx_id)
        if not tx:
            raise ValueError(f"tx not found: {tx_id[:16]}...")
        if tx.tx_type != TxType.SHARE:
            raise ValueError("not a SHARE transaction")
        if tx.recipient != w.address:
            raise ValueError("wallet is not the recipient")
        ct_hex = tx.payload.get("encapsulatedKey", "")
        if not ct_hex:
            raise ValueError("no encapsulatedKey in tx")
        ciphertext = bytes.fromhex(ct_hex)
        shared_secret = MLKEM.decapsulate(w.encryption_sk, ciphertext)
        return {"shared_secret": shared_secret.hex()}

    async def _rpc_send_raw_tx(self, tx_data=None):
        if not isinstance(tx_data, dict):
            raise ValueError("tx_data must be a JSON object")
        tx = Transaction.from_dict(tx_data)
        ok, result = self.blockchain.submit_tx(tx)
        if not ok:
            raise ValueError(result)
        await self.p2p.broadcast(MSG_NEW_TX, {"tx": tx.to_dict()})
        return {"tx_id": result}

    async def _rpc_new_wallet(self):
        w = Wallet.generate()
        self.wallets[w.address] = w
        self._save_wallets()
        return {
            "address": w.address,
            "signing_pk": w.signing_pk.hex(),
            "encryption_pk": w.encryption_pk.hex(),
        }

    async def _rpc_list_wallets(self):
        return list(self.wallets.keys())

    async def _rpc_get_wallet_keys(self, address=""):
        if not isinstance(address, str):
            raise ValueError("address must be string")
        w = self.wallets.get(address)
        if not w:
            raise ValueError(f"wallet not found: {address[:16]}...")
        return {
            "address": w.address,
            "signing_pk": w.signing_pk.hex(),
            "encryption_pk": w.encryption_pk.hex(),
        }

    # ================================================================
    # Helpers
    # ================================================================

    def _lock_genesis_if_needed(self):
        """Lock genesis hash once we have a chain, preventing replacement."""
        bc = self.blockchain
        if bc.chain and not bc.consensus._genesis_hash:
            bc.consensus.set_genesis_hash(bc.chain[0].block_hash)

    def _get_wallet(self, address: str) -> Wallet:
        if not isinstance(address, str):
            raise ValueError("wallet address must be string")
        w = self.wallets.get(address)
        if not w:
            raise ValueError(f"wallet not found: {address[:16]}...")
        return w

    def _lock_for(self, address: str) -> asyncio.Lock:
        """Get or create a per-address lock for atomic nonce+submit."""
        if address not in self._wallet_locks:
            self._wallet_locks[address] = asyncio.Lock()
        return self._wallet_locks[address]

    # ================================================================
    # Lifecycle
    # ================================================================

    async def start(self, validator_wallet: Wallet | None = None):
        logger.info("=" * 60)
        logger.info("  QBit Network PQC Blockchain Node")
        logger.info("=" * 60)

        # Load persisted wallets
        self._load_wallets()

        # Register validators BEFORE loading chain so block sigs are verified on load
        if validator_wallet:
            self.validator_wallet = validator_wallet
            self.wallets[validator_wallet.address] = validator_wallet
            self.blockchain.consensus.add_validator(
                validator_wallet.address, validator_wallet.signing_pk)

        # Load chain (validators are now known → block signatures are verified)
        loaded = self.blockchain.load()

        if validator_wallet and not loaded:
            self.blockchain.init_chain(
                validator_wallet.address, validator_wallet.signing_sk)

            # Auto-register validator's encryption_pk on-chain
            reg_tx = Transaction.register_key(
                validator_wallet.address, validator_wallet.encryption_pk, nonce=0)
            reg_tx.sign(validator_wallet.signing_sk, validator_wallet.signing_pk)
            self.blockchain.submit_tx(reg_tx)

        if self.blockchain.chain:
            self.blockchain.consensus.set_genesis_hash(
                self.blockchain.chain[0].block_hash)
        elif not validator_wallet:
            # Non-validator with no chain — accept genesis from first sync.
            # Genesis hash stays "" so the first valid genesis is accepted.
            # Once accepted, lock it immediately in _p2p_blocks handler.
            logger.warning("No chain and no validator — will sync from peers")

        self._register_p2p()
        self._register_rpc()

        await self.p2p.start()
        await self.rpc.start()

        for addr in self.bootstrap:
            parts = addr.split(":")
            if len(parts) == 2:
                try:
                    await self.p2p.connect(parts[0], int(parts[1]))
                except ValueError:
                    pass

        # Initial sync with request-ID correlation
        if self.p2p.peer_count() > 0:
            await self.p2p.broadcast(MSG_STATUS, {
                "height": self.blockchain.height})
            req_id = secrets.token_hex(8)
            self.p2p._pending_requests[req_id] = time.time()
            await self.p2p.broadcast(MSG_GET_BLOCKS, {
                "from": len(self.blockchain.chain), "count": 50,
                "request_id": req_id})

        self._running = True
        self._save_wallets()

        if self.validator_wallet:
            self._block_task = asyncio.create_task(self._block_loop())
        self._sync_task = asyncio.create_task(self._sync_loop())

        vaddr = self.validator_wallet.address if self.validator_wallet else "none"
        logger.info(f"Validator: {vaddr}")
        logger.info(f"Chain height: {self.blockchain.height}")
        logger.info(f"P2P: {self.p2p.port} | RPC: {self.rpc.port}")
        logger.info(f"Wallets loaded: {len(self.wallets)}")

    async def stop(self):
        if not self._running:
            return
        self._running = False
        for task in (self._block_task, self._sync_task):
            if task and not task.done():
                task.cancel()
        await self.p2p.stop()
        await self.rpc.stop()
        self.blockchain.save()
        self._save_wallets()
        logger.info("Node stopped")

    async def _block_loop(self):
        while self._running:
            await asyncio.sleep(BLOCK_INTERVAL)
            if not self._running:
                break
            try:
                w = self.validator_wallet
                block = self.blockchain.produce_block(w.address, w.signing_sk)
                if block:
                    await self.p2p.broadcast(
                        MSG_NEW_BLOCK, {"block": block.to_dict()})
                    if block.index % 20 == 0:
                        self.blockchain.save()
            except Exception as e:
                logger.error(f"Block production error: {e}")

    async def run(self):
        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
