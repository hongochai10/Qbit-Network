"""REST API gateway — proxies to existing node methods with proper HTTP semantics."""
import hmac
import logging
import re
import time as _time
from aiohttp import web

logger = logging.getLogger("qbit_network.rest_api")

# Pagination defaults
_DEFAULT_PAGE = 1
_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100

# Token endpoint pagination defaults (R31-002)
_TOKEN_DEFAULT_LIMIT = 100
_TOKEN_MAX_LIMIT = 1000


def _ok(data, status: int = 200) -> web.Response:
    """Return a success JSON response."""
    return web.json_response({"data": data, "error": None}, status=status)


def _err(code: int, message: str, status: int = 400) -> web.Response:
    """Return an error JSON response."""
    return web.json_response(
        {"data": None, "error": {"code": code, "message": message}},
        status=status,
    )


def _parse_pagination(request: web.Request) -> tuple[int, int] | web.Response:
    """Extract page and limit from query params. Returns (page, limit) or error Response."""
    try:
        page = int(request.query.get("page", _DEFAULT_PAGE))
    except (ValueError, TypeError):
        return _err(400, "page must be an integer")
    try:
        limit = int(request.query.get("limit", _DEFAULT_LIMIT))
    except (ValueError, TypeError):
        return _err(400, "limit must be an integer")

    if page < 1:
        return _err(400, "page must be >= 1")
    if limit < 1 or limit > _MAX_LIMIT:
        return _err(400, f"limit must be 1-{_MAX_LIMIT}")

    return page, limit


def _parse_token_pagination(request: web.Request) -> tuple[int, int] | web.Response:
    """Extract offset and limit for token endpoints (R31-002).

    Returns (offset, limit) or error Response.
    """
    try:
        offset = int(request.query.get("offset", 0))
    except (ValueError, TypeError):
        return _err(400, "offset must be an integer")
    try:
        limit = int(request.query.get("limit", _TOKEN_DEFAULT_LIMIT))
    except (ValueError, TypeError):
        return _err(400, "limit must be an integer")

    if offset < 0:
        return _err(400, "offset must be >= 0")
    if limit < 1 or limit > _TOKEN_MAX_LIMIT:
        return _err(400, f"limit must be 1-{_TOKEN_MAX_LIMIT}")

    return offset, limit


class RESTApi:
    """RESTful API gateway mounted as an aiohttp sub-application at /api/v1/.

    All handlers delegate to the FullNode instance — no business logic duplicated.
    """

    def __init__(self, node, auth_token: str, cors_origins: str = ""):
        """
        Parameters
        ----------
        node : FullNode
            The running node instance whose methods we proxy.
        auth_token : str
            Bearer token for protected endpoints (same as RPC token).
        cors_origins : str
            Allowed CORS origins (default "" = no cross-origin access).
            Use "*" for development or a specific origin like
            "https://example.com" for production.
        """
        self._node = node
        self._auth_token = auth_token
        self._cors_origins = cors_origins
        self.app = web.Application(
            middlewares=[self._cors_middleware, self._auth_middleware],
            client_max_size=1048576,  # 1 MB, matching MAX_RPC_BODY
        )
        self._register_routes()

    # ------------------------------------------------------------------
    # Middleware
    # ------------------------------------------------------------------

    @web.middleware
    async def _cors_middleware(self, request: web.Request, handler):
        """Add CORS headers and handle OPTIONS preflight.

        When cors_origins is empty (default), no CORS headers are added and
        OPTIONS preflight returns 403 — effectively blocking cross-origin
        browser requests.
        """
        if not self._cors_origins:
            # Restrictive mode: no CORS headers at all
            if request.method == "OPTIONS":
                return web.Response(status=403, text="CORS not allowed")
            try:
                return await handler(request)
            except web.HTTPException as exc:
                return exc

        if request.method == "OPTIONS":
            resp = web.Response(status=204)
        else:
            try:
                resp = await handler(request)
            except web.HTTPException as exc:
                resp = exc
        resp.headers["Access-Control-Allow-Origin"] = self._cors_origins
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        # Only expose Authorization header when a specific origin is configured;
        # wildcard origins should not receive auth headers (SPRINT2-002).
        if self._cors_origins == "*":
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        else:
            resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        resp.headers["Access-Control-Max-Age"] = "86400"
        return resp

    # ------------------------------------------------------------------
    # Auth middleware (route-group enforcement)
    # ------------------------------------------------------------------

    # Routes that do NOT require authentication.
    # Matched against the aiohttp resource canonical path (e.g. "/info",
    # "/blocks/{index}").  Any route not listed here is protected.
    _PUBLIC_ROUTES: set[str] = {
        "/info",
        "/health",
        "/health/ready",
        "/metrics",
        "/blocks",
        "/blocks/latest",
        "/blocks/hash/{hash}",
        "/blocks/{index}",
        "/txs/sender/{addr}",
        "/txs/{txid}",
        "/address/{addr}",
        "/notarizations/{hash}",
        "/validators",
        "/stakes",
        "/stakes/{validator}",
        "/balance/{addr}",
        "/supply",
        "/fee",
        "/pool",
        "/pool/count",
        "/verify",
        "/epochs/current",
        "/slashing-events",
        "/state-proof/{addr}",
        "/state-root",
        "/receipt/{txid}",
        "/events",
        "/finalized",
        # Token read endpoints
        "/tokens",
        "/tokens/{token_id}",
        "/tokens/{token_id}/holders",
        "/address/{addr}/tokens",
        # Light client endpoints
        "/headers",
        "/headers/{index}",
        "/proofs/state/{key}",
        "/proofs/receipt/{txid}",
    }

    @web.middleware
    async def _auth_middleware(self, request: web.Request, handler):
        """Enforce bearer-token auth on all non-public routes."""
        resource = request.match_info.route.resource
        canonical = resource.canonical if resource else None
        # When mounted as sub-app at /api/v1/, canonical includes the
        # prefix.  Strip any leading /api/v1 so the set lookup works.
        route_path = canonical
        if canonical and canonical.startswith("/api/v1"):
            route_path = canonical[len("/api/v1"):]  # e.g. "/address/{addr}"
        if route_path not in self._PUBLIC_ROUTES:
            if not self._check_auth(request):
                return _err(401, "authentication required", status=401)
        return await handler(request)

    # ------------------------------------------------------------------
    # Auth helper
    # ------------------------------------------------------------------

    def _check_auth(self, request: web.Request) -> bool:
        """Verify bearer token. Returns True if valid."""
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return False
        token = auth[7:].strip()
        if not token:
            return False
        return hmac.compare_digest(token, self._auth_token)

    # ------------------------------------------------------------------
    # Route registration
    # ------------------------------------------------------------------

    def _register_routes(self):
        r = self.app.router

        # Public endpoints
        r.add_get("/info", self._info)
        r.add_get("/health", self._health)
        r.add_get("/health/ready", self._readiness)
        r.add_get("/metrics", self._metrics)
        r.add_get("/blocks", self._list_blocks)
        r.add_get("/blocks/latest", self._latest_block)
        r.add_get("/blocks/hash/{hash}", self._block_by_hash)
        r.add_get("/blocks/{index}", self._block_by_index)
        r.add_get("/txs/sender/{addr}", self._txs_by_sender)
        r.add_get("/txs/{txid}", self._get_tx)
        r.add_get("/address/{addr}", self._address_info)
        r.add_get("/notarizations/{hash}", self._notarization_proof)
        r.add_get("/validators", self._validators)
        r.add_get("/stakes", self._all_stakes)
        r.add_get("/stakes/{validator}", self._validator_stake)
        r.add_get("/balance/{addr}", self._get_balance)
        r.add_get("/supply", self._get_supply)
        r.add_get("/fee", self._get_fee_info)
        r.add_get("/pool", self._pool_summary)
        r.add_get("/pool/count", self._pool_count)
        r.add_post("/verify", self._verify)  # public -- no auth needed
        r.add_get("/epochs/current", self._current_epoch)
        r.add_get("/slashing-events", self._slashing_events)
        r.add_get("/state-proof/{addr}", self._state_proof)
        r.add_get("/state-root", self._state_root)
        r.add_get("/receipt/{txid}", self._get_receipt)
        r.add_get("/events", self._get_events)
        r.add_get("/finalized", self._get_finalized)

        # Webhooks (protected)
        r.add_post("/webhooks", self._register_webhook)
        r.add_get("/webhooks", self._list_webhooks)
        r.add_delete("/webhooks/{id}", self._delete_webhook)

        # Protected endpoints
        r.add_post("/transfer", self._transfer_rest)
        r.add_post("/txs", self._submit_tx)
        r.add_post("/evidence", self._submit_evidence)
        r.add_post("/wallets", self._create_wallet)
        r.add_get("/wallets", self._list_wallets)
        r.add_post("/notarize", self._notarize)
        r.add_post("/store", self._store)
        r.add_post("/share", self._share)
        r.add_post("/register-validator", self._register_validator)
        r.add_post("/stake", self._stake_rest)
        r.add_post("/delegate", self._delegate_rest)
        r.add_post("/unstake", self._unstake_rest)

        # Token endpoints (public)
        r.add_get("/tokens", self._list_tokens)
        r.add_get("/tokens/{token_id}", self._get_token)
        r.add_get("/tokens/{token_id}/holders", self._get_token_holders)
        r.add_get("/address/{addr}/tokens", self._get_address_tokens)
        # Token endpoints (protected)
        r.add_post("/issue-token", self._issue_token_rest)
        r.add_post("/mint-token", self._mint_token_rest)
        r.add_post("/transfer-token", self._transfer_token_rest)

        # Light client endpoints (public)
        r.add_get("/headers", self._list_headers)
        r.add_get("/headers/{index}", self._get_header)
        r.add_get("/proofs/state/{key}", self._state_proof_by_key)
        r.add_get("/proofs/receipt/{txid}", self._receipt_proof)

    # ------------------------------------------------------------------
    # Public handlers
    # ------------------------------------------------------------------

    async def _info(self, request: web.Request) -> web.Response:
        info = await self._node._rpc_node_info()
        return _ok(info)

    async def _health(self, request: web.Request) -> web.Response:
        node = self._node
        bc = node.blockchain
        height = bc.height
        peers = node.p2p.peer_count()

        # Last block timestamp
        last_block_time = None
        if height >= 0:
            last_block = bc.get_block(height)
            if last_block:
                last_block_time = getattr(last_block, "timestamp", None)

        # Sync status: synced if no peers report a higher chain
        best_peer = node.p2p.best_peer_height()
        synced = best_peer <= height

        return _ok({
            "status": "ok",
            "chain_height": height,
            "peers": peers,
            "last_block_time": last_block_time,
            "synced": synced,
            "tx_pool_size": len(bc.tx_pool),
        })

    async def _readiness(self, request: web.Request) -> web.Response:
        """Kubernetes readiness probe — returns 200 only when synced with peers."""
        node = self._node
        height = node.blockchain.height
        peers = node.p2p.peer_count()
        best_peer = node.p2p.best_peer_height()
        synced = best_peer <= height
        ready = synced and (peers > 0 or height >= 0)
        status_code = 200 if ready else 503
        body = {
            "ready": ready,
            "chain_height": height,
            "peers": peers,
            "synced": synced,
        }
        return web.json_response(
            {"data": body, "error": None}, status=status_code)

    async def _metrics(self, request: web.Request) -> web.Response:
        """Prometheus text exposition format."""
        registry = getattr(self._node, "metrics", None)
        if registry is None:
            return web.Response(
                status=503, text="# Metrics not available\n",
                content_type="text/plain")
        text = registry.generate_text()
        resp = web.Response(text=text, content_type="text/plain")
        resp.headers["Content-Type"] = "text/plain; version=0.0.4; charset=utf-8"
        return resp

    async def _list_blocks(self, request: web.Request) -> web.Response:
        pag = _parse_pagination(request)
        if isinstance(pag, web.Response):
            return pag
        page, limit = pag

        bc = self._node.blockchain
        total = bc.height + 1 if bc.height >= 0 else 0
        start = (page - 1) * limit
        end = min(start + limit, total)

        blocks = []
        if start < total:
            for i in range(start, end):
                block = bc.get_block(i)
                if block is not None:
                    bd = block.to_dict()
                    bd.pop("transactions", None)  # R15-005: headers only; full tx via /blocks/{index}
                    blocks.append(bd)

        return _ok({
            "blocks": blocks,
            "total": total,
            "page": page,
            "limit": limit,
        })

    async def _latest_block(self, request: web.Request) -> web.Response:
        block = self._node.blockchain.latest_block
        if not block:
            return _err(404, "no blocks in chain", status=404)
        return _ok(block.to_dict())

    async def _block_by_index(self, request: web.Request) -> web.Response:
        raw = request.match_info["index"]
        try:
            index = int(raw)
        except ValueError:
            return _err(400, "index must be an integer")
        if index < 0:
            return _err(400, "index must be >= 0")

        block = self._node.blockchain.get_block(index)
        if not block:
            return _err(404, f"block {index} not found", status=404)
        return _ok(block.to_dict())

    async def _block_by_hash(self, request: web.Request) -> web.Response:
        block_hash = request.match_info["hash"]
        if not block_hash:
            return _err(400, "hash is required")

        # Validate hex-only to prevent reflected user input in errors
        if not re.match(r'^[0-9a-fA-F]+$', block_hash):
            return _err(400, "invalid hash format")

        block = self._node.blockchain.get_block(block_hash)
        if not block:
            return _err(404, "block not found", status=404)
        return _ok(block.to_dict())

    async def _get_tx(self, request: web.Request) -> web.Response:
        txid = request.match_info["txid"]
        if not txid:
            return _err(400, "txid is required")

        result = await self._node._rpc_get_tx(tx_id=txid)
        if result is None:
            return _err(404, f"transaction {txid[:16]}... not found", status=404)
        return _ok(result)

    async def _txs_by_sender(self, request: web.Request) -> web.Response:
        addr = request.match_info["addr"]
        if not addr:
            return _err(400, "address is required")

        pag = _parse_pagination(request)
        if isinstance(pag, web.Response):
            return pag
        page, limit = pag

        bc = self._node.blockchain
        total = bc.get_txs_by_sender_count(addr)
        start = (page - 1) * limit

        tx_ids_page = bc.get_txs_by_sender_page(addr, start, limit) if start < total else []
        txs = []
        for tid in tx_ids_page:
            tx = self._node.blockchain.get_tx(tid)
            if tx:
                d = tx.to_dict()
                d["block_index"] = self._node.blockchain.get_tx_block(tid)
                txs.append(d)

        return _ok({
            "transactions": txs,
            "total": total,
            "page": page,
            "limit": limit,
        })

    async def _address_info(self, request: web.Request) -> web.Response:
        addr = request.match_info["addr"]
        if not addr:
            return _err(400, "address is required")

        bc = self._node.blockchain
        nonce = bc.get_nonce(addr)
        encryption_pk = bc.get_encryption_pk(addr)
        sent_count = bc.get_txs_by_sender_count(addr)
        received_count = bc.get_txs_by_recipient_count(addr)

        # Use O(1) notarization counter instead of iterating all sender txs
        notarization_count = bc.get_notarization_count(addr)

        is_validator = bc.is_registered_validator(addr)
        balance = bc.get_balance(addr)

        return _ok({
            "address": addr,
            "balance": balance,
            "next_nonce": nonce,
            "encryption_pk": encryption_pk,
            "sent_tx_count": sent_count,
            "received_tx_count": received_count,
            "notarization_count": notarization_count,
            "is_validator": is_validator,
        })

    async def _notarization_proof(self, request: web.Request) -> web.Response:
        doc_hash = request.match_info["hash"]
        if not doc_hash:
            return _err(400, "document hash is required")

        result = self._node.blockchain.verify_document(doc_hash)
        if result is None:
            return _err(404, f"no notarization found for hash {doc_hash[:16]}...", status=404)

        # Include all notarizations
        all_notarizations = self._node.blockchain.get_all_notarizations(doc_hash)

        return _ok({
            "first_notarization": result,
            "all_notarizations": all_notarizations,
            "total": len(all_notarizations),
        })

    async def _validators(self, request: web.Request) -> web.Response:
        result = await self._node._rpc_validators()
        return _ok({"validators": result, "total": len(result)})

    async def _pool_summary(self, request: web.Request) -> web.Response:
        pool = self._node.blockchain.tx_pool
        # Summary without full tx data
        by_type: dict[str, int] = {}
        for tx in pool:
            t = tx.tx_type.value
            by_type[t] = by_type.get(t, 0) + 1

        return _ok({
            "count": len(pool),
            "by_type": by_type,
        })

    async def _pool_count(self, request: web.Request) -> web.Response:
        return _ok({"count": len(self._node.blockchain.tx_pool)})

    async def _current_epoch(self, request: web.Request) -> web.Response:
        result = await self._node._rpc_get_epoch()
        return _ok(result)

    async def _slashing_events(self, request: web.Request) -> web.Response:
        validator = request.query.get("validator", "")
        result = await self._node._rpc_get_slashing_events(validator)
        return _ok(result)

    async def _state_proof(self, request: web.Request) -> web.Response:
        addr = request.match_info.get("addr", "")
        if not addr:
            return _err(400, "address is required")
        key_type = request.query.get("key", "balance")
        try:
            result = await self._node._rpc_get_state_proof(
                address=addr, key_type=key_type)
            return _ok(result)
        except ValueError as e:
            return _err(400, str(e))
        except Exception as e:
            logger.error(f"REST state_proof: {e}")
            return _err(500, "internal server error", status=500)

    async def _state_root(self, request: web.Request) -> web.Response:
        try:
            result = await self._node._rpc_get_state_root()
            return _ok(result)
        except Exception as e:
            logger.error(f"REST state_root: {e}")
            return _err(500, "internal server error", status=500)

    async def _get_balance(self, request: web.Request) -> web.Response:
        addr = request.match_info.get("addr", "")
        if not addr:
            return _err(400, "address is required")
        try:
            result = await self._node._rpc_get_balance(address=addr)
            return _ok(result)
        except ValueError as e:
            return _err(400, str(e))
        except Exception as e:
            logger.error(f"REST get_balance: {e}")
            return _err(500, "internal server error", status=500)

    async def _get_supply(self, request: web.Request) -> web.Response:
        try:
            result = await self._node._rpc_get_supply()
            return _ok(result)
        except Exception as e:
            logger.error(f"REST get_supply: {e}")
            return _err(500, "internal server error", status=500)

    async def _get_fee_info(self, request: web.Request) -> web.Response:
        try:
            result = await self._node._rpc_get_fee_info()
            return _ok(result)
        except Exception as e:
            logger.error(f"REST get_fee_info: {e}")
            return _err(500, "internal server error", status=500)

    async def _get_receipt(self, request: web.Request) -> web.Response:
        txid = request.match_info.get("txid", "")
        if not txid:
            return _err(400, "txid is required")
        # Validate hex-only
        if not re.match(r'^[0-9a-fA-F]+$', txid):
            return _err(400, "invalid txid format")
        try:
            result = await self._node._rpc_get_receipt(tx_id=txid)
            if result is None:
                return _err(404, f"receipt for {txid[:16]}... not found", status=404)
            return _ok(result)
        except ValueError as e:
            return _err(400, str(e))
        except Exception as e:
            logger.error(f"REST get_receipt: {e}")
            return _err(500, "internal server error", status=500)

    async def _get_events(self, request: web.Request) -> web.Response:
        event_type = request.query.get("type", "")
        sender = request.query.get("from", request.query.get("sender", ""))
        try:
            limit = int(request.query.get("limit", 20))
        except (ValueError, TypeError):
            return _err(400, "limit must be an integer")
        if limit < 1 or limit > _MAX_LIMIT:
            return _err(400, f"limit must be 1-{_MAX_LIMIT}")
        block_index_str = request.query.get("block_index")
        block_index = None
        if block_index_str is not None:
            try:
                block_index = int(block_index_str)
            except (ValueError, TypeError):
                return _err(400, "block_index must be an integer")
        try:
            result = await self._node._rpc_get_logs(
                event_type=event_type,
                block_index=block_index,
                sender=sender,
                limit=limit,
            )
            return _ok({"events": result, "total": len(result)})
        except ValueError as e:
            return _err(400, str(e))
        except Exception as e:
            logger.error(f"REST get_events: {e}")
            return _err(500, "internal server error", status=500)

    async def _get_finalized(self, request: web.Request) -> web.Response:
        try:
            result = await self._node._rpc_get_finalized()
            return _ok(result)
        except Exception as e:
            logger.error(f"REST get_finalized: {e}")
            return _err(500, "internal server error", status=500)

    # ------------------------------------------------------------------
    # Protected handlers
    # ------------------------------------------------------------------

    async def _transfer_rest(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _err(400, "invalid JSON body")
        if not isinstance(body, dict):
            return _err(400, "request body must be a JSON object")

        wallet_address = body.get("wallet_address", "")
        to_address = body.get("to_address", "")
        amount = body.get("amount", 0)
        memo = body.get("memo", "")

        if not isinstance(wallet_address, str) or not wallet_address:
            return _err(400, "wallet_address is required and must be a string")
        if not isinstance(to_address, str) or not to_address:
            return _err(400, "to_address is required and must be a string")
        if not isinstance(amount, int) or amount <= 0:
            return _err(400, "amount must be a positive integer")
        if not isinstance(memo, str):
            return _err(400, "memo must be a string")

        try:
            result = await self._node._rpc_transfer(
                wallet_address=wallet_address,
                to_address=to_address,
                amount=amount,
                memo=memo,
            )
            return _ok(result, status=201)
        except ValueError as e:
            return _err(400, str(e))
        except Exception as e:
            logger.error(f"REST transfer: {e}")
            return _err(500, "internal server error", status=500)

    async def _submit_evidence(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _err(400, "invalid JSON body")
        if not isinstance(body, dict):
            return _err(400, "body must be JSON object")

        _ALLOWED = {
            "wallet_address", "validator_address", "block_index",
            "block_a_hash", "block_b_hash", "block_a_sig", "block_b_sig",
            "block_a_header", "block_b_header",
            "max_fee_per_weight", "max_priority_fee",
        }
        unknown = set(body.keys()) - _ALLOWED
        if unknown:
            return _err(400, f"unknown fields: {sorted(unknown)}")

        wallet_address = body.get("wallet_address", "")
        validator_address = body.get("validator_address", "")
        block_index = body.get("block_index", 0)
        block_a_hash = body.get("block_a_hash", "")
        block_b_hash = body.get("block_b_hash", "")
        block_a_sig = body.get("block_a_sig", "")
        block_b_sig = body.get("block_b_sig", "")
        block_a_header = body.get("block_a_header", "")
        block_b_header = body.get("block_b_header", "")
        max_fee_per_weight = body.get("max_fee_per_weight")
        max_priority_fee = body.get("max_priority_fee")

        if not isinstance(wallet_address, str) or not wallet_address:
            return _err(400, "wallet_address is required and must be a string")
        if not isinstance(validator_address, str) or not validator_address:
            return _err(400, "validator_address is required and must be a string")
        if not isinstance(block_index, int) or block_index < 0:
            return _err(400, "block_index must be a non-negative integer")
        for fname, fval in [("block_a_hash", block_a_hash), ("block_b_hash", block_b_hash),
                            ("block_a_sig", block_a_sig), ("block_b_sig", block_b_sig),
                            ("block_a_header", block_a_header), ("block_b_header", block_b_header)]:
            if not isinstance(fval, str) or not fval:
                return _err(400, f"{fname} must be a non-empty string")

        try:
            result = await self._node._rpc_submit_evidence(
                wallet_address=wallet_address,
                validator_address=validator_address,
                block_index=block_index,
                block_a_hash=block_a_hash,
                block_b_hash=block_b_hash,
                block_a_sig=block_a_sig,
                block_b_sig=block_b_sig,
                block_a_header=block_a_header,
                block_b_header=block_b_header,
                max_fee_per_weight=max_fee_per_weight,
                max_priority_fee=max_priority_fee,
            )
            return _ok(result, status=201)
        except ValueError as e:
            return _err(400, str(e))
        except Exception as e:
            logger.error(f"POST /evidence: {e}")
            return _err(500, "internal error", status=500)

    async def _submit_tx(self, request: web.Request) -> web.Response:

        try:
            body = await request.json()
        except Exception:
            return _err(400, "invalid JSON body")

        if not isinstance(body, dict):
            return _err(400, "request body must be a JSON object")

        try:
            result = await self._node._rpc_send_raw_tx(tx_data=body)
            return _ok(result, status=201)
        except ValueError as e:
            return _err(400, str(e))
        except Exception as e:
            logger.error(f"REST submit_tx: {e}")
            return _err(500, "internal server error", status=500)

    async def _create_wallet(self, request: web.Request) -> web.Response:

        try:
            result = await self._node._rpc_new_wallet()
            return _ok(result, status=201)
        except Exception as e:
            logger.error(f"REST create_wallet: {e}")
            return _err(500, "internal server error", status=500)

    async def _list_wallets(self, request: web.Request) -> web.Response:

        try:
            result = await self._node._rpc_list_wallets()
            return _ok({"wallets": result, "total": len(result)})
        except Exception as e:
            logger.error(f"REST list_wallets: {e}")
            return _err(500, "internal server error", status=500)

    async def _notarize(self, request: web.Request) -> web.Response:

        try:
            body = await request.json()
        except Exception:
            return _err(400, "invalid JSON body")

        if not isinstance(body, dict):
            return _err(400, "request body must be a JSON object")

        wallet_address = body.get("wallet_address", "")
        document_hash = body.get("document_hash", "")
        metadata = body.get("metadata", "")

        if not isinstance(wallet_address, str) or not wallet_address:
            return _err(400, "wallet_address is required and must be a string")
        if not isinstance(document_hash, str) or not document_hash:
            return _err(400, "document_hash is required and must be a string")
        if not isinstance(metadata, str):
            return _err(400, "metadata must be a string")

        try:
            result = await self._node._rpc_notarize(
                wallet_address=wallet_address,
                document_hash=document_hash,
                metadata=metadata,
            )
            return _ok(result, status=201)
        except ValueError as e:
            return _err(400, str(e))
        except Exception as e:
            logger.error(f"REST notarize: {e}")
            return _err(500, "internal server error", status=500)

    async def _verify(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _err(400, "invalid JSON body")

        if not isinstance(body, dict):
            return _err(400, "request body must be a JSON object")

        document_hash = body.get("document_hash", "")
        if not isinstance(document_hash, str) or not document_hash:
            return _err(400, "document_hash is required and must be a string")

        try:
            result = await self._node._rpc_verify_document(document_hash=document_hash)
            if result is None:
                return _ok({"verified": False, "proof": None})
            return _ok({"verified": True, "proof": result})
        except ValueError as e:
            return _err(400, str(e))
        except Exception as e:
            logger.error(f"REST verify: {e}")
            return _err(500, "internal server error", status=500)

    async def _store(self, request: web.Request) -> web.Response:

        try:
            body = await request.json()
        except Exception:
            return _err(400, "invalid JSON body")

        if not isinstance(body, dict):
            return _err(400, "request body must be a JSON object")

        wallet_address = body.get("wallet_address", "")
        document_hash = body.get("document_hash", "")
        cid = body.get("cid", "")
        metadata = body.get("metadata", "")

        if not isinstance(wallet_address, str) or not wallet_address:
            return _err(400, "wallet_address is required and must be a string")
        if not isinstance(document_hash, str) or not document_hash:
            return _err(400, "document_hash is required and must be a string")
        if not isinstance(cid, str):
            return _err(400, "cid must be a string")
        if not isinstance(metadata, str):
            return _err(400, "metadata must be a string")

        try:
            result = await self._node._rpc_store(
                wallet_address=wallet_address,
                document_hash=document_hash,
                cid=cid,
                metadata=metadata,
            )
            return _ok(result, status=201)
        except ValueError as e:
            return _err(400, str(e))
        except Exception as e:
            logger.error(f"REST store: {e}")
            return _err(500, "internal server error", status=500)

    async def _share(self, request: web.Request) -> web.Response:

        try:
            body = await request.json()
        except Exception:
            return _err(400, "invalid JSON body")

        if not isinstance(body, dict):
            return _err(400, "request body must be a JSON object")

        wallet_address = body.get("wallet_address", "")
        recipient_address = body.get("recipient_address", "")
        cid = body.get("cid", "")
        recipient_encryption_pk = body.get("recipient_encryption_pk", "")
        expires = body.get("expires", 0)

        if not isinstance(wallet_address, str) or not wallet_address:
            return _err(400, "wallet_address is required and must be a string")
        if not isinstance(recipient_address, str) or not recipient_address:
            return _err(400, "recipient_address is required and must be a string")
        if not isinstance(cid, str):
            return _err(400, "cid must be a string")
        if not isinstance(recipient_encryption_pk, str):
            return _err(400, "recipient_encryption_pk must be a string")
        if not isinstance(expires, int):
            return _err(400, "expires must be an integer")

        try:
            result = await self._node._rpc_share(
                wallet_address=wallet_address,
                recipient_address=recipient_address,
                cid=cid,
                recipient_encryption_pk=recipient_encryption_pk,
                expires=expires,
            )
            return _ok(result, status=201)
        except ValueError as e:
            return _err(400, str(e))
        except Exception as e:
            logger.error(f"REST share: {e}")
            return _err(500, "internal server error", status=500)

    async def _register_validator(self, request: web.Request) -> web.Response:

        try:
            body = await request.json()
        except Exception:
            return _err(400, "invalid JSON body")

        if not isinstance(body, dict):
            return _err(400, "request body must be a JSON object")

        wallet_address = body.get("wallet_address", "")
        if not isinstance(wallet_address, str) or not wallet_address:
            return _err(400, "wallet_address is required and must be a string")

        try:
            result = await self._node._rpc_register_validator(
                wallet_address=wallet_address,
            )
            return _ok(result, status=201)
        except ValueError as e:
            return _err(400, str(e))
        except Exception as e:
            logger.error(f"REST register_validator: {e}")
            return _err(500, "internal server error", status=500)

    # ------------------------------------------------------------------
    # Staking endpoints (public reads + protected writes)
    # ------------------------------------------------------------------

    async def _all_stakes(self, request: web.Request) -> web.Response:
        try:
            result = await self._node._rpc_get_validator_stakes()
            return _ok({"validators": result, "total": len(result)})
        except Exception as e:
            logger.error(f"REST all_stakes: {e}")
            return _err(500, "internal server error", status=500)

    async def _validator_stake(self, request: web.Request) -> web.Response:
        validator = request.match_info.get("validator", "")
        if not validator:
            return _err(400, "validator address required")
        try:
            result = await self._node._rpc_get_stake(validator_address=validator)
            return _ok(result)
        except ValueError as e:
            return _err(400, str(e))
        except Exception as e:
            logger.error(f"REST validator_stake: {e}")
            return _err(500, "internal server error", status=500)

    async def _stake_rest(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _err(400, "invalid JSON body")
        if not isinstance(body, dict):
            return _err(400, "request body must be a JSON object")

        wallet_address = body.get("wallet_address", "")
        validator_address = body.get("validator_address", "")
        amount = body.get("amount", 0)

        if not isinstance(wallet_address, str) or not wallet_address:
            return _err(400, "wallet_address is required and must be a string")
        if not isinstance(validator_address, str) or not validator_address:
            return _err(400, "validator_address is required and must be a string")
        if not isinstance(amount, int) or amount < 1:
            return _err(400, "amount must be a positive integer")

        try:
            result = await self._node._rpc_stake(
                wallet_address=wallet_address,
                validator_address=validator_address,
                amount=amount,
            )
            return _ok(result, status=201)
        except ValueError as e:
            return _err(400, str(e))
        except Exception as e:
            logger.error(f"REST stake: {e}")
            return _err(500, "internal server error", status=500)

    async def _delegate_rest(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _err(400, "invalid JSON body")
        if not isinstance(body, dict):
            return _err(400, "request body must be a JSON object")

        wallet_address = body.get("wallet_address", "")
        validator_address = body.get("validator_address", "")
        amount = body.get("amount", 0)

        if not isinstance(wallet_address, str) or not wallet_address:
            return _err(400, "wallet_address is required and must be a string")
        if not isinstance(validator_address, str) or not validator_address:
            return _err(400, "validator_address is required and must be a string")
        if not isinstance(amount, int) or amount < 1:
            return _err(400, "amount must be a positive integer")

        try:
            result = await self._node._rpc_delegate(
                wallet_address=wallet_address,
                validator_address=validator_address,
                amount=amount,
            )
            return _ok(result, status=201)
        except ValueError as e:
            return _err(400, str(e))
        except Exception as e:
            logger.error(f"REST delegate: {e}")
            return _err(500, "internal server error", status=500)

    async def _unstake_rest(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _err(400, "invalid JSON body")
        if not isinstance(body, dict):
            return _err(400, "request body must be a JSON object")

        wallet_address = body.get("wallet_address", "")
        validator_address = body.get("validator_address", "")
        amount = body.get("amount", 0)

        if not isinstance(wallet_address, str) or not wallet_address:
            return _err(400, "wallet_address is required and must be a string")
        if not isinstance(validator_address, str) or not validator_address:
            return _err(400, "validator_address is required and must be a string")
        if not isinstance(amount, int) or amount < 1:
            return _err(400, "amount must be a positive integer")

        try:
            result = await self._node._rpc_unstake(
                wallet_address=wallet_address,
                validator_address=validator_address,
                amount=amount,
            )
            return _ok(result, status=201)
        except ValueError as e:
            return _err(400, str(e))
        except Exception as e:
            logger.error(f"REST unstake: {e}")
            return _err(500, "internal server error", status=500)

    # ------------------------------------------------------------------
    # Webhook handlers
    # ------------------------------------------------------------------

    async def _register_webhook(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _err(400, "invalid JSON body")
        if not isinstance(body, dict):
            return _err(400, "request body must be a JSON object")

        url = body.get("url", "")
        events = body.get("events", [])
        secret = body.get("secret", "")

        if not isinstance(url, str) or not url:
            return _err(400, "url is required and must be a string")
        if not isinstance(events, list) or not events:
            return _err(400, "events is required and must be a non-empty list")
        if not isinstance(secret, str) or not secret:
            return _err(400, "secret is required and must be a non-empty string")

        try:
            result = self._node.webhook_manager.register(url, events, secret)
            return _ok(result, status=201)
        except ValueError as e:
            return _err(400, str(e))
        except Exception as e:
            logger.error(f"REST register_webhook: {e}")
            return _err(500, "internal server error", status=500)

    async def _list_webhooks(self, request: web.Request) -> web.Response:
        try:
            webhooks = self._node.webhook_manager.list_webhooks()
            return _ok({"webhooks": webhooks, "total": len(webhooks)})
        except Exception as e:
            logger.error(f"REST list_webhooks: {e}")
            return _err(500, "internal server error", status=500)

    async def _delete_webhook(self, request: web.Request) -> web.Response:
        webhook_id = request.match_info.get("id", "")
        if not webhook_id:
            return _err(400, "webhook id is required")

        try:
            deleted = self._node.webhook_manager.delete(webhook_id)
            if not deleted:
                return _err(404, "webhook not found", status=404)
            return _ok({"deleted": True})
        except ValueError as e:
            return _err(400, str(e))
        except Exception as e:
            logger.error(f"REST delete_webhook: {e}")
            return _err(500, "internal server error", status=500)

    # ------------------------------------------------------------------
    # Token handlers
    # ------------------------------------------------------------------

    async def _list_tokens(self, request: web.Request) -> web.Response:
        pag = _parse_token_pagination(request)
        if isinstance(pag, web.Response):
            return pag
        offset, limit = pag
        bc = self._node.blockchain
        page = (offset // limit) + 1
        tokens = bc.list_tokens(page=page, limit=limit)
        return _ok({"tokens": tokens, "offset": offset, "limit": limit,
                     "total": len(bc._token_registry)})

    async def _get_token(self, request: web.Request) -> web.Response:
        token_id = request.match_info.get("token_id", "")
        if not token_id:
            return _err(400, "token_id is required")
        bc = self._node.blockchain
        info = bc.get_token_info(token_id)
        if info is None:
            return _err(404, "token not found", status=404)
        return _ok(info)

    async def _get_token_holders(self, request: web.Request) -> web.Response:
        token_id = request.match_info.get("token_id", "")
        if not token_id:
            return _err(400, "token_id is required")
        bc = self._node.blockchain
        if token_id not in bc._token_registry:
            return _err(404, "token not found", status=404)
        pag = _parse_token_pagination(request)
        if isinstance(pag, web.Response):
            return pag
        offset, limit = pag
        page = (offset // limit) + 1
        holders, total = bc.get_token_holders(token_id, page=page, limit=limit)
        return _ok({"token_id": token_id, "holders": holders,
                     "total": total, "offset": offset, "limit": limit})

    async def _get_address_tokens(self, request: web.Request) -> web.Response:
        addr = request.match_info.get("addr", "")
        if not addr:
            return _err(400, "address is required")
        bc = self._node.blockchain
        pag = _parse_token_pagination(request)
        if isinstance(pag, web.Response):
            return pag
        offset, limit = pag
        page = (offset // limit) + 1
        tokens = bc.get_address_tokens(addr, page=page, limit=limit)
        return _ok({"address": addr, "tokens": tokens,
                     "offset": offset, "limit": limit})

    async def _issue_token_rest(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _err(400, "invalid JSON body")

        wallet_name = body.get("wallet", "")
        name = body.get("name", "")
        symbol = body.get("symbol", "")
        decimals = body.get("decimals")
        max_supply = body.get("max_supply", 0)
        transferable = body.get("transferable", True)

        if not wallet_name:
            return _err(400, "wallet is required")
        if not name or not symbol or decimals is None:
            return _err(400, "name, symbol, and decimals are required")

        try:
            result = await self._node._rpc_issue_token(
                wallet_name, name, symbol, decimals, max_supply, transferable)
            return _ok(result, status=201)
        except ValueError as e:
            return _err(400, str(e))
        except Exception as e:
            logger.error(f"REST issue_token: {e}")
            return _err(500, "internal server error", status=500)

    async def _mint_token_rest(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _err(400, "invalid JSON body")

        wallet_name = body.get("wallet", "")
        token_id = body.get("token_id", "")
        recipient = body.get("recipient", "")
        amount = body.get("amount")

        if not wallet_name or not token_id or not recipient or not amount:
            return _err(400, "wallet, token_id, recipient, and amount are required")

        try:
            result = await self._node._rpc_mint_token(
                wallet_name, token_id, recipient, amount)
            return _ok(result, status=201)
        except ValueError as e:
            return _err(400, str(e))
        except Exception as e:
            logger.error(f"REST mint_token: {e}")
            return _err(500, "internal server error", status=500)

    async def _transfer_token_rest(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _err(400, "invalid JSON body")

        wallet_name = body.get("wallet", "")
        token_id = body.get("token_id", "")
        recipient = body.get("recipient", "")
        amount = body.get("amount")
        memo = body.get("memo", "")

        if not wallet_name or not token_id or not recipient or not amount:
            return _err(400, "wallet, token_id, recipient, and amount are required")

        try:
            result = await self._node._rpc_transfer_token(
                wallet_name, token_id, recipient, amount, memo)
            return _ok(result, status=201)
        except ValueError as e:
            return _err(400, str(e))
        except Exception as e:
            logger.error(f"REST transfer_token: {e}")
            return _err(500, "internal server error", status=500)

    # ------------------------------------------------------------------
    # Light client handlers
    # ------------------------------------------------------------------

    async def _list_headers(self, request: web.Request) -> web.Response:
        try:
            start = int(request.query.get("start", 0))
            count = int(request.query.get("count", 20))
        except (ValueError, TypeError):
            return _err(400, "start and count must be integers")
        if start < 0:
            return _err(400, "start must be >= 0")
        if count < 1 or count > 100:
            return _err(400, "count must be 1-100")
        bc = self._node.blockchain
        headers = bc.get_block_headers(start=start, count=count)
        return _ok({"headers": headers, "start": start,
                     "count": len(headers), "height": bc.height})

    async def _get_header(self, request: web.Request) -> web.Response:
        try:
            index = int(request.match_info.get("index", -1))
        except (ValueError, TypeError):
            return _err(400, "index must be an integer")
        bc = self._node.blockchain
        block = bc._get_block_by_index(index)
        if block is None:
            return _err(404, "block not found", status=404)
        return _ok(block.to_header_dict())

    # Key prefixes allowed without authentication for state proofs
    _PUBLIC_PROOF_PREFIXES = ("balance:", "nonce:")
    # Key prefixes that require authentication
    _AUTHED_PROOF_PREFIXES = ("token:",)

    async def _state_proof_by_key(self, request: web.Request) -> web.Response:
        key = request.match_info.get("key", "")
        if not key:
            return _err(400, "key is required")
        # Restrict key prefixes: only balance:/nonce: are public;
        # token:* requires auth; all others are denied.
        is_public = any(key.startswith(p) for p in self._PUBLIC_PROOF_PREFIXES)
        is_authed_prefix = any(key.startswith(p) for p in self._AUTHED_PROOF_PREFIXES)
        if not is_public:
            if is_authed_prefix:
                if not self._check_auth(request):
                    return _err(403, "authentication required for token state proofs", status=403)
            else:
                return _err(403, "state proof key prefix not allowed", status=403)
        block_str = request.query.get("block")
        block_index = None
        if block_str is not None:
            try:
                block_index = int(block_str)
            except (ValueError, TypeError):
                return _err(400, "block must be an integer")
        bc = self._node.blockchain
        proof = bc.get_state_proof_at_block(key, block_index)
        if proof is None:
            return _err(404, "key not found or block snapshot unavailable", status=404)
        return _ok(proof)

    async def _receipt_proof(self, request: web.Request) -> web.Response:
        txid = request.match_info.get("txid", "")
        if not txid:
            return _err(400, "txid is required")
        bc = self._node.blockchain
        proof = bc.get_receipt_proof(txid)
        if proof is None:
            return _err(404, "receipt not found", status=404)
        return _ok(proof)
