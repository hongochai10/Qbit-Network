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
from .network.rest_api import RESTApi
from .network.websocket import WebSocketManager
from .config import DEFAULT_P2P_PORT, DEFAULT_RPC_PORT, BLOCK_INTERVAL, VERSION

logger = logging.getLogger("qbit_network.node")

_MAX_SHARED_SECRETS = 10000


class FullNode:
    """QVault full node."""

    def __init__(self, *, host: str = "0.0.0.0", p2p_port: int = DEFAULT_P2P_PORT,
                 rpc_port: int = DEFAULT_RPC_PORT, data_dir: str = "",
                 bootstrap: list[str] | None = None, rpc_token: str = "",
                 tls_cert: str = "", tls_key: str = "",
                 tls_self_signed: bool = False, tls_auto: bool = False,
                 tls_hostname: str = "localhost"):
        self.data_dir = data_dir
        self.blockchain = Blockchain(data_dir=data_dir)
        self.wallets: dict[str, Wallet] = {}
        self.validator_wallet: Wallet | None = None

        self.p2p = P2PNode(host, p2p_port, node_id=secrets.token_hex(16))
        self.rpc = RPCServer(host, rpc_port, auth_token=rpc_token,
                             tls_cert=tls_cert, tls_key=tls_key,
                             tls_self_signed=tls_self_signed, data_dir=data_dir,
                             tls_auto=tls_auto, tls_hostname=tls_hostname)
        self.ws_manager = WebSocketManager()
        self.bootstrap = bootstrap or []
        self._running = False
        self._block_task = None
        self._sync_task = None
        self._shared_secrets: collections.OrderedDict[str, bytes] = collections.OrderedDict()
        self._wallet_locks: collections.OrderedDict[str, asyncio.Lock] = collections.OrderedDict()  # per-address tx submission lock

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
                self.p2p.reputation.record(peer.addr, "valid_block")
                self._lock_genesis_if_needed()
                await self.p2p.broadcast(
                    MSG_NEW_BLOCK, {"block": block.to_dict()}, exclude=peer.addr)
                await self._ws_notify_block(block)
            else:
                self.p2p.reputation.record(peer.addr, "invalid_block")
        except Exception as e:
            self.p2p.reputation.record(peer.addr, "invalid_block")
            logger.debug(f"bad block from {peer.addr}: {e}")

    async def _p2p_new_tx(self, peer, data):
        try:
            tx = Transaction.from_dict(data["tx"])
            ok, _ = self.blockchain.submit_tx(tx)
            if ok:
                self.p2p.reputation.record(peer.addr, "valid_tx")
                await self.p2p.broadcast(
                    MSG_NEW_TX, {"tx": tx.to_dict()}, exclude=peer.addr)
                await self._ws_notify_tx(tx)
            else:
                self.p2p.reputation.record(peer.addr, "invalid_tx")
        except Exception:
            self.p2p.reputation.record(peer.addr, "invalid_tx")

    async def _p2p_get_blocks(self, peer, data):
        try:
            start = max(0, int(data.get("from", 0)))
            count = min(int(data.get("count", 50)), 100)
        except (TypeError, ValueError):
            return
        chain_len = self.blockchain.height + 1
        blocks = []
        for i in range(start, min(start + count, chain_len)):
            block = self.blockchain.get_block(i)
            if block is not None:
                blocks.append(block.to_dict())
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
        m("qv_getStake", self._rpc_get_stake)
        m("qv_getValidatorStakes", self._rpc_get_validator_stakes)
        m("qv_getEpoch", self._rpc_get_epoch)
        m("qv_getSlashingEvents", self._rpc_get_slashing_events)
        m("qv_getBalance", self._rpc_get_balance)
        m("qv_getSupply", self._rpc_get_supply)
        m("qv_getFeeInfo", self._rpc_get_fee_info)

        # Protected (require auth token)
        m("qv_notarize", self._rpc_notarize)
        m("qv_store", self._rpc_store)
        m("qv_share", self._rpc_share)
        m("qv_registerKey", self._rpc_register_key)
        m("qv_registerValidator", self._rpc_register_validator)
        m("qv_revokeKey", self._rpc_revoke_key)
        m("qv_stake", self._rpc_stake)
        m("qv_delegate", self._rpc_delegate)
        m("qv_unstake", self._rpc_unstake)
        m("qv_submitEvidence", self._rpc_submit_evidence)
        m("qv_transfer", self._rpc_transfer)
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

        # Add effective fee for transactions in confirmed blocks
        if block_idx is not None:
            from .config import DYNAMIC_FEE_ACTIVATION_HEIGHT
            from .core.fees import compute_tx_fee, tx_weight
            if block_idx >= DYNAMIC_FEE_ACTIVATION_HEIGHT:
                block = self.blockchain.get_block(block_idx)
                if block is not None:
                    w = tx_weight(tx.tx_type.value)
                    if w > 0:
                        eff_fee = compute_tx_fee(
                            block.base_fee, tx.max_fee_per_weight,
                            tx.max_priority_fee, w)
                        result["effectiveFee"] = eff_fee

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
        all_validators = set(self.blockchain.consensus.validators.keys())
        all_validators.update(self.blockchain._validator_registry.keys())
        return {
            "version": VERSION,
            "chain_height": self.blockchain.height,
            "pending_txs": len(self.blockchain.tx_pool),
            "peers": self.p2p.peer_count(),
            "validator": self.validator_wallet.address if self.validator_wallet else None,
            "validators": sorted(all_validators),
            "registered_validators": sorted(self.blockchain._validator_registry.keys()),
            "wallets": len(self.wallets),
        }

    async def _rpc_validators(self):
        # Include both config-based and on-chain registered validators
        all_validators = set(self.blockchain.consensus.validators.keys())
        all_validators.update(self.blockchain._validator_registry.keys())
        return sorted(all_validators)

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
        await self._ws_notify_tx(tx)
        return {"tx_id": result}

    async def _rpc_register_validator(self, wallet_address=""):
        """Register a wallet's signing key as a validator on-chain.

        The wallet signs the tx, proving ownership of the validator key.
        The validator_address in payload is derived from the signing pubkey.
        """
        w = self._get_wallet(wallet_address)
        if self.blockchain.is_registered_validator(w.address):
            raise ValueError(f"validator already registered: {w.address[:16]}...")
        async with self._lock_for(w.address):
            tx = Transaction.register_validator(
                sender=w.address,
                validator_pubkey=w.signing_pk,
                validator_address=w.address,
                nonce=self._next_nonce(w.address),
            )
            tx.sign(w.signing_sk, w.signing_pk)
            ok, result = self.blockchain.submit_tx(tx)
        if not ok:
            raise ValueError(result)
        await self.p2p.broadcast(MSG_NEW_TX, {"tx": tx.to_dict()})
        await self._ws_notify_tx(tx)
        return {"tx_id": result, "validator_address": w.address}

    async def _rpc_revoke_key(self, wallet_address="", key_type="", reason=""):
        """Revoke a key on-chain. Self-revocation only."""
        if not isinstance(key_type, str):
            raise ValueError("key_type must be string")
        if not isinstance(reason, str):
            raise ValueError("reason must be string")
        valid_key_types = {"signing", "encryption", "validator"}
        valid_reasons = {"compromised", "rotation", "decommission"}
        if key_type not in valid_key_types:
            raise ValueError(f"key_type must be one of {sorted(valid_key_types)}")
        if reason not in valid_reasons:
            raise ValueError(f"reason must be one of {sorted(valid_reasons)}")
        w = self._get_wallet(wallet_address)
        async with self._lock_for(w.address):
            tx = Transaction.revoke_key(
                sender=w.address,
                key_type=key_type,
                reason=reason,
                nonce=self._next_nonce(w.address),
            )
            tx.sign(w.signing_sk, w.signing_pk)
            ok, result = self.blockchain.submit_tx(tx)
        if not ok:
            raise ValueError(result)
        await self.p2p.broadcast(MSG_NEW_TX, {"tx": tx.to_dict()})
        await self._ws_notify_tx(tx)
        return {"tx_id": result, "key_type": key_type, "reason": reason}

    async def _rpc_stake(self, wallet_address="", validator_address="", amount=0):
        """Stake weight on a validator (self-stake)."""
        if not isinstance(validator_address, str) or not validator_address:
            raise ValueError("validator_address must be non-empty string")
        if not isinstance(amount, int) or amount < 1:
            raise ValueError("amount must be positive integer")
        w = self._get_wallet(wallet_address)
        async with self._lock_for(w.address):
            tx = Transaction.stake(
                sender=w.address,
                validator_address=validator_address,
                amount=amount,
                nonce=self._next_nonce(w.address),
            )
            tx.sign(w.signing_sk, w.signing_pk)
            ok, result = self.blockchain.submit_tx(tx)
        if not ok:
            raise ValueError(result)
        await self.p2p.broadcast(MSG_NEW_TX, {"tx": tx.to_dict()})
        await self._ws_notify_tx(tx)
        return {"tx_id": result, "validator_address": validator_address, "amount": amount}

    async def _rpc_delegate(self, wallet_address="", validator_address="", amount=0):
        """Delegate stake weight to a validator."""
        if not isinstance(validator_address, str) or not validator_address:
            raise ValueError("validator_address must be non-empty string")
        if not isinstance(amount, int) or amount < 1:
            raise ValueError("amount must be positive integer")
        w = self._get_wallet(wallet_address)
        async with self._lock_for(w.address):
            tx = Transaction.delegate(
                sender=w.address,
                validator_address=validator_address,
                amount=amount,
                nonce=self._next_nonce(w.address),
            )
            tx.sign(w.signing_sk, w.signing_pk)
            ok, result = self.blockchain.submit_tx(tx)
        if not ok:
            raise ValueError(result)
        await self.p2p.broadcast(MSG_NEW_TX, {"tx": tx.to_dict()})
        await self._ws_notify_tx(tx)
        return {"tx_id": result, "validator_address": validator_address, "amount": amount}

    async def _rpc_unstake(self, wallet_address="", validator_address="", amount=0):
        """Begin unstaking weight from a validator."""
        if not isinstance(validator_address, str) or not validator_address:
            raise ValueError("validator_address must be non-empty string")
        if not isinstance(amount, int) or amount < 1:
            raise ValueError("amount must be positive integer")
        w = self._get_wallet(wallet_address)
        async with self._lock_for(w.address):
            tx = Transaction.unstake(
                sender=w.address,
                validator_address=validator_address,
                amount=amount,
                nonce=self._next_nonce(w.address),
            )
            tx.sign(w.signing_sk, w.signing_pk)
            ok, result = self.blockchain.submit_tx(tx)
        if not ok:
            raise ValueError(result)
        await self.p2p.broadcast(MSG_NEW_TX, {"tx": tx.to_dict()})
        await self._ws_notify_tx(tx)
        return {"tx_id": result, "validator_address": validator_address, "amount": amount}

    async def _rpc_get_stake(self, validator_address=""):
        """Get total stake for a validator (public)."""
        if not isinstance(validator_address, str):
            raise ValueError("validator_address must be string")
        total = self.blockchain.get_validator_stake(validator_address)
        stakers = self.blockchain._stakes.get(validator_address, {})
        return {
            "validator_address": validator_address,
            "total_stake": total,
            "stakers": dict(stakers),
        }

    async def _rpc_get_validator_stakes(self):
        """Get all validators with their stake info (public)."""
        active = self.blockchain.get_active_validators()
        return [{"validator": addr, "total_stake": total} for addr, total in active]

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
        await self._ws_notify_tx(tx)
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
        await self._ws_notify_tx(tx)
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
        await self._ws_notify_tx(tx)

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
        await self._ws_notify_tx(tx)
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

    # ---- Balance & Transfer RPC ----

    async def _rpc_get_balance(self, address=""):
        """Get balance for an address (public)."""
        if not isinstance(address, str) or not address:
            raise ValueError("address must be non-empty string")
        from .config import QUBIT_PER_QBIT, TOKEN_SYMBOL
        balance = self.blockchain.get_balance(address)
        return {
            "address": address,
            "balance": balance,
            "formatted": f"{balance / QUBIT_PER_QBIT:.8f} {TOKEN_SYMBOL}",
        }

    async def _rpc_get_supply(self):
        """Get total supply info (public)."""
        from .config import (QUBIT_PER_QBIT, TOKEN_SYMBOL, MAX_SUPPLY,
                             DYNAMIC_FEE_ACTIVATION_HEIGHT)
        supply = self.blockchain.get_total_supply()
        bc = self.blockchain
        dynamic_active = (bc.height >= 0
                          and bc.height + 1 >= DYNAMIC_FEE_ACTIVATION_HEIGHT)
        return {
            "total_minted": supply["total_minted"],
            "total_burned": supply["total_burned"],
            "circulating": supply["circulating"],
            "max_supply": MAX_SUPPLY,
            "formatted_circulating": f"{supply['circulating'] / QUBIT_PER_QBIT:.8f} {TOKEN_SYMBOL}",
            "fee_model": "dynamic" if dynamic_active else "fixed",
        }

    async def _rpc_get_fee_info(self):
        """Get current dynamic fee information (public)."""
        from .config import (TX_WEIGHTS, DYNAMIC_FEE_ACTIVATION_HEIGHT,
                             INITIAL_BASE_FEE)
        from .core.fees import compute_base_fee, effective_block_weight

        bc = self.blockchain
        dynamic_active = (bc.height >= 0
                          and bc.height + 1 >= DYNAMIC_FEE_ACTIVATION_HEIGHT)

        if dynamic_active:
            current_bf = bc._current_base_fee()
            # Compute next_base_fee from current tip
            parent = bc.latest_block
            if parent is not None:
                parent_eff = effective_block_weight(
                    parent.transactions, parent.validator)
                next_bf = compute_base_fee(current_bf, parent_eff)
            else:
                next_bf = INITIAL_BASE_FEE
        else:
            current_bf = 0
            next_bf = INITIAL_BASE_FEE

        suggested_priority = max(1, current_bf // 10)

        # Build estimated fees per TX type
        estimated = {}
        for tx_type, weight in TX_WEIGHTS.items():
            if weight == 0:
                estimated[tx_type] = {"min": 0, "suggested": 0}
            else:
                min_fee = current_bf * weight
                suggested_fee = (current_bf + suggested_priority) * weight
                estimated[tx_type] = {"min": min_fee, "suggested": suggested_fee}

        return {
            "base_fee": current_bf,
            "next_base_fee": next_bf,
            "suggested_priority_fee": suggested_priority,
            "weights": dict(TX_WEIGHTS),
            "estimated_fees": estimated,
            "fee_model": "dynamic" if dynamic_active else "fixed",
        }

    async def _rpc_transfer(self, wallet_address="", to_address="",
                            amount=0, memo=""):
        """Transfer QBIT tokens (protected)."""
        if not isinstance(wallet_address, str) or not wallet_address:
            raise ValueError("wallet_address must be non-empty string")
        if not isinstance(to_address, str) or not to_address:
            raise ValueError("to_address must be non-empty string")
        if not isinstance(amount, int) or amount <= 0:
            raise ValueError("amount must be a positive integer")
        if not isinstance(memo, str):
            raise ValueError("memo must be a string")
        if len(memo) > 256:
            raise ValueError("memo exceeds 256 characters")
        w = self._get_wallet(wallet_address)
        if w.address == to_address:
            raise ValueError("cannot transfer to self")
        async with self._lock_for(w.address):
            tx = Transaction.transfer(
                sender=w.address,
                recipient=to_address,
                amount=amount,
                memo=memo,
                nonce=self._next_nonce(w.address),
            )
            tx.sign(w.signing_sk, w.signing_pk)
            ok, result = self.blockchain.submit_tx(tx)
        if not ok:
            raise ValueError(result)
        await self.p2p.broadcast(MSG_NEW_TX, {"tx": tx.to_dict()})
        await self._ws_notify_tx(tx)
        return {"tx_id": result, "amount": amount, "to": to_address}

    # ---- Epoch & Slashing RPC ----

    async def _rpc_get_epoch(self):
        """Get current epoch info (public)."""
        epoch = self.blockchain.get_current_epoch()
        validators = self.blockchain.get_epoch_validators(epoch)
        return {
            "epoch": epoch,
            "validators": [{"address": addr, "stake": stake} for addr, stake in validators],
        }

    async def _rpc_get_slashing_events(self, validator=""):
        """Get slashing events, optionally filtered by validator (public)."""
        if not isinstance(validator, str):
            raise ValueError("validator must be string")
        return self.blockchain.get_slashing_events(validator)

    async def _rpc_submit_evidence(self, wallet_address="", validator_address="",
                                   block_index=0, block_a_hash="", block_b_hash="",
                                   block_a_sig="", block_b_sig="",
                                   block_a_header="", block_b_header=""):
        """Submit double-sign evidence (protected)."""
        if not isinstance(wallet_address, str) or not wallet_address:
            raise ValueError("wallet_address required")
        if not isinstance(validator_address, str) or not validator_address:
            raise ValueError("validator_address required")
        if not isinstance(block_index, int) or block_index < 0:
            raise ValueError("block_index must be non-negative integer")
        for name, val in [("block_a_hash", block_a_hash), ("block_b_hash", block_b_hash),
                          ("block_a_sig", block_a_sig), ("block_b_sig", block_b_sig),
                          ("block_a_header", block_a_header), ("block_b_header", block_b_header)]:
            if not isinstance(val, str) or not val:
                raise ValueError(f"{name} must be non-empty string")

        w = self._get_wallet(wallet_address)
        async with self._lock_for(w.address):
            tx = Transaction.evidence(
                sender=w.address,
                validator_address=validator_address,
                block_index=block_index,
                block_a_hash=block_a_hash,
                block_b_hash=block_b_hash,
                block_a_sig=block_a_sig,
                block_b_sig=block_b_sig,
                block_a_header=block_a_header,
                block_b_header=block_b_header,
                nonce=self._next_nonce(w.address),
            )
            tx.sign(w.signing_sk, w.signing_pk)
            ok, result = self.blockchain.submit_tx(tx)
        if not ok:
            raise ValueError(result)
        from .network.p2p import MSG_NEW_TX
        await self.p2p.broadcast(MSG_NEW_TX, {"tx": tx.to_dict()})
        return {"tx_id": result, "validator_address": validator_address}

    # ================================================================
    # Helpers
    # ================================================================

    def _lock_genesis_if_needed(self):
        """Lock genesis hash once we have a chain, preventing replacement."""
        bc = self.blockchain
        if bc.height >= 0 and not bc.consensus._genesis_hash:
            genesis = bc.get_block(0)
            if genesis is not None:
                bc.consensus.set_genesis_hash(genesis.block_hash)

    def _get_wallet(self, address: str) -> Wallet:
        if not isinstance(address, str):
            raise ValueError("wallet address must be string")
        w = self.wallets.get(address)
        if not w:
            raise ValueError(f"wallet not found: {address[:16]}...")
        return w

    _MAX_WALLET_LOCKS = 10_000

    def _lock_for(self, address: str) -> asyncio.Lock:
        """Get or create a per-address lock for atomic nonce+submit.

        Bounded to _MAX_WALLET_LOCKS entries; oldest are evicted on overflow (R15-004).
        """
        if address in self._wallet_locks:
            # Move to end (most recently used)
            self._wallet_locks.move_to_end(address)
            return self._wallet_locks[address]
        lock = asyncio.Lock()
        self._wallet_locks[address] = lock
        while len(self._wallet_locks) > self._MAX_WALLET_LOCKS:
            self._wallet_locks.popitem(last=False)
        return lock

    # ================================================================
    # WebSocket event helpers
    # ================================================================

    def _get_chain_stats(self) -> dict:
        """Build chain_stats payload for WS broadcast."""
        return {
            "height": self.blockchain.height,
            "tx_count": len(self.blockchain._tx_by_id),
            "pool_size": len(self.blockchain.tx_pool),
            "peers": self.p2p.peer_count(),
        }

    async def _ws_notify_block(self, block: Block):
        """Broadcast a new_block event over WebSocket."""
        try:
            await self.ws_manager.broadcast("new_block", block.to_dict())
        except Exception as e:
            logger.debug(f"WS new_block broadcast error: {e}")

    async def _ws_notify_tx(self, tx: Transaction):
        """Broadcast a new_tx event over WebSocket."""
        try:
            await self.ws_manager.broadcast("new_tx", tx.to_dict())
        except Exception as e:
            logger.debug(f"WS new_tx broadcast error: {e}")

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
            # Enable P2P authentication with validator keys
            self.p2p.signing_sk = validator_wallet.signing_sk
            self.p2p.signing_pk = validator_wallet.signing_pk
            self.p2p.validator_address = validator_wallet.address
            # Enable P2P encrypted channel with encryption keys
            self.p2p.encryption_sk = validator_wallet.encryption_sk
            self.p2p.encryption_pk = validator_wallet.encryption_pk

        # Load chain (validators are now known → block signatures are verified)
        loaded = self.blockchain.load()

        if validator_wallet and not loaded:
            self.blockchain.init_chain(
                validator_wallet.address, validator_wallet.signing_sk,
                validator_pk=validator_wallet.signing_pk)

            # Activate financial layer with genesis balance allocation
            self.blockchain.activate_financial_layer(validator_wallet.address)

            # Auto-register validator's encryption_pk on-chain
            reg_tx = Transaction.register_key(
                validator_wallet.address, validator_wallet.encryption_pk, nonce=0)
            reg_tx.sign(validator_wallet.signing_sk, validator_wallet.signing_pk)
            self.blockchain.submit_tx(reg_tx)

        if self.blockchain.height >= 0:
            genesis = self.blockchain.get_block(0)
            if genesis is not None:
                self.blockchain.consensus.set_genesis_hash(genesis.block_hash)
        elif not validator_wallet:
            # Non-validator with no chain — accept genesis from first sync.
            # Genesis hash stays "" so the first valid genesis is accepted.
            # Once accepted, lock it immediately in _p2p_blocks handler.
            logger.warning("No chain and no validator — will sync from peers")

        self._register_p2p()
        self._register_rpc()

        # Mount REST API gateway as sub-app at /api/v1/
        self._rest_api = RESTApi(self, self.rpc.auth_token)
        self.rpc._app.add_subapp("/api/v1/", self._rest_api.app)

        # Attach WebSocket manager to RPC server
        self.ws_manager._get_stats_fn = self._get_chain_stats
        self.rpc.attach_websocket(self.ws_manager)

        await self.p2p.start()
        await self.rpc.start()
        await self.ws_manager.start_stats_loop()

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
                "from": self.blockchain.height + 1, "count": 50,
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
        await self.ws_manager.stop()
        await self.p2p.stop()
        await self.rpc.stop()
        self.blockchain.save()
        self._save_wallets()
        # Zero all wallet secret key material
        for w in self.wallets.values():
            if hasattr(w, 'close'):
                w.close()
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
                    await self._ws_notify_block(block)
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
