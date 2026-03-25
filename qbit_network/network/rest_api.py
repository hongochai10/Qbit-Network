"""REST API gateway — proxies to existing node methods with proper HTTP semantics."""
import hmac
import logging
import re
from aiohttp import web

logger = logging.getLogger("qbit_network.rest_api")

# Pagination defaults
_DEFAULT_PAGE = 1
_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100


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


class RESTApi:
    """RESTful API gateway mounted as an aiohttp sub-application at /api/v1/.

    All handlers delegate to the FullNode instance — no business logic duplicated.
    """

    def __init__(self, node, auth_token: str, cors_origins: str = "*"):
        """
        Parameters
        ----------
        node : FullNode
            The running node instance whose methods we proxy.
        auth_token : str
            Bearer token for protected endpoints (same as RPC token).
        cors_origins : str
            Allowed CORS origins (default "*").
        """
        self._node = node
        self._auth_token = auth_token
        self._cors_origins = cors_origins
        self.app = web.Application(
            middlewares=[self._cors_middleware],
            client_max_size=1048576,  # 1 MB, matching MAX_RPC_BODY
        )
        self._register_routes()

    # ------------------------------------------------------------------
    # Middleware
    # ------------------------------------------------------------------

    @web.middleware
    async def _cors_middleware(self, request: web.Request, handler):
        """Add CORS headers and handle OPTIONS preflight."""
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
        r.add_get("/pool", self._pool_summary)
        r.add_get("/pool/count", self._pool_count)
        r.add_post("/verify", self._verify)  # public -- no auth needed

        # Protected endpoints
        r.add_post("/txs", self._submit_tx)
        r.add_post("/wallets", self._create_wallet)
        r.add_get("/wallets", self._list_wallets)
        r.add_post("/notarize", self._notarize)
        r.add_post("/store", self._store)
        r.add_post("/share", self._share)
        r.add_post("/register-validator", self._register_validator)
        r.add_post("/stake", self._stake_rest)
        r.add_post("/delegate", self._delegate_rest)
        r.add_post("/unstake", self._unstake_rest)

    # ------------------------------------------------------------------
    # Public handlers
    # ------------------------------------------------------------------

    async def _info(self, request: web.Request) -> web.Response:
        info = await self._node._rpc_node_info()
        return _ok(info)

    async def _health(self, request: web.Request) -> web.Response:
        return _ok({"status": "ok"})

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
                    blocks.append(block.to_dict())

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

        all_tx_ids = self._node.blockchain.get_txs_by_sender(addr)
        total = len(all_tx_ids)
        start = (page - 1) * limit
        end = min(start + limit, total)

        tx_ids_page = all_tx_ids[start:end] if start < total else []
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
        sent_tx_ids = bc.get_txs_by_sender(addr)
        received_tx_ids = bc.get_txs_by_recipient(addr)

        # Use O(1) notarization counter instead of iterating all sender txs
        notarization_count = bc.get_notarization_count(addr)

        is_validator = bc.is_registered_validator(addr)

        return _ok({
            "address": addr,
            "next_nonce": nonce,
            "encryption_pk": encryption_pk,
            "sent_tx_count": len(sent_tx_ids),
            "received_tx_count": len(received_tx_ids),
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

    # ------------------------------------------------------------------
    # Protected handlers
    # ------------------------------------------------------------------

    async def _submit_tx(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return _err(401, "authentication required", status=401)

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
        if not self._check_auth(request):
            return _err(401, "authentication required", status=401)

        try:
            result = await self._node._rpc_new_wallet()
            return _ok(result, status=201)
        except Exception as e:
            logger.error(f"REST create_wallet: {e}")
            return _err(500, "internal server error", status=500)

    async def _list_wallets(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return _err(401, "authentication required", status=401)

        try:
            result = await self._node._rpc_list_wallets()
            return _ok({"wallets": result, "total": len(result)})
        except Exception as e:
            logger.error(f"REST list_wallets: {e}")
            return _err(500, "internal server error", status=500)

    async def _notarize(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return _err(401, "authentication required", status=401)

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
        if not self._check_auth(request):
            return _err(401, "authentication required", status=401)

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
        if not self._check_auth(request):
            return _err(401, "authentication required", status=401)

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
        if not self._check_auth(request):
            return _err(401, "authentication required", status=401)

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
        if not self._check_auth(request):
            return _err(401, "authentication required", status=401)
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
        if not self._check_auth(request):
            return _err(401, "authentication required", status=401)
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
        if not self._check_auth(request):
            return _err(401, "authentication required", status=401)
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
