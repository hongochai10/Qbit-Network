"""QBit Network SDK client — stdlib only (urllib.request)."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
import urllib.error
from typing import Any

from .models import (
    Block, Transaction, Wallet, NodeInfo, BalanceInfo,
    SupplyInfo, FeeInfo, StateProof, Receipt, VerifyResult, Webhook,
)
from .exceptions import (
    QBitError, AuthenticationError, NotFoundError,
    InsufficientBalance, ValidationError,
)

_DEFAULT_TIMEOUT = 30  # seconds


class QBitClient:
    """Synchronous Python client for the QBit Network REST API.

    Parameters
    ----------
    base_url : str
        Base URL of the node REST API (e.g. ``http://localhost:8545/api/v1``).
    token : str
        Bearer token for protected endpoints.
    timeout : int
        HTTP request timeout in seconds.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8545/api/v1",
        token: str = "",
        timeout: int = _DEFAULT_TIMEOUT,
    ):
        # Strip trailing slash
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self, auth: bool = False) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if auth and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        auth: bool = False,
        params: dict[str, str] | None = None,
    ) -> Any:
        """Make an HTTP request and return the ``data`` field from the response."""
        url = f"{self._base_url}{path}"
        if params:
            # URL-encode parameter values to prevent injection (R18-002)
            qs = urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None})
            if qs:
                url = f"{url}?{qs}"

        data_bytes = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers=self._headers(auth=auth),
            method=method,
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                resp_body = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            try:
                err_body = json.loads(exc.read().decode())
                err_msg = err_body.get("error", {}).get("message", str(exc))
            except Exception:
                err_msg = str(exc)

            if exc.code == 401:
                raise AuthenticationError(err_msg) from exc
            if exc.code == 404:
                raise NotFoundError(err_msg) from exc
            if exc.code == 400:
                lower = err_msg.lower()
                if "insufficient" in lower or "balance" in lower:
                    raise InsufficientBalance(err_msg) from exc
                raise ValidationError(err_msg) from exc
            raise QBitError(err_msg, code=exc.code) from exc
        except urllib.error.URLError as exc:
            raise QBitError(f"connection error: {exc.reason}") from exc

        if resp_body.get("error") is not None:
            err = resp_body["error"]
            raise QBitError(err.get("message", "unknown error"), code=err.get("code", 0))

        return resp_body.get("data")

    def _get(self, path: str, *, auth: bool = False,
             params: dict[str, str] | None = None) -> Any:
        return self._request("GET", path, auth=auth, params=params)

    def _post(self, path: str, body: dict, *, auth: bool = False) -> Any:
        return self._request("POST", path, body=body, auth=auth)

    def _delete(self, path: str, *, auth: bool = False) -> Any:
        return self._request("DELETE", path, auth=auth)

    # ------------------------------------------------------------------
    # Public endpoints
    # ------------------------------------------------------------------

    def get_health(self) -> dict:
        """Check node health. Returns ``{"status": "ok"}``."""
        return self._get("/health")

    def get_info(self) -> NodeInfo:
        """Get node information."""
        return NodeInfo.from_dict(self._get("/info"))

    def get_block(self, index: int) -> Block:
        """Get a block by index (includes transactions)."""
        return Block.from_dict(self._get(f"/blocks/{index}"))

    def get_block_by_hash(self, block_hash: str) -> Block:
        """Get a block by hash."""
        return Block.from_dict(self._get(f"/blocks/hash/{block_hash}"))

    def get_latest_block(self) -> Block:
        """Get the latest block."""
        return Block.from_dict(self._get("/blocks/latest"))

    def list_blocks(self, page: int = 1, limit: int = 20) -> dict:
        """List block headers (paginated).

        Returns dict with keys: blocks, total, page, limit.
        """
        data = self._get("/blocks", params={"page": str(page), "limit": str(limit)})
        data["blocks"] = [Block.from_dict(b) for b in data.get("blocks", [])]
        return data

    def get_transaction(self, tx_id: str) -> Transaction:
        """Get a transaction by ID."""
        return Transaction.from_dict(self._get(f"/txs/{tx_id}"))

    def get_txs_by_sender(self, address: str, page: int = 1,
                          limit: int = 20) -> dict:
        """List transactions by sender (paginated)."""
        data = self._get(
            f"/txs/sender/{address}",
            params={"page": str(page), "limit": str(limit)},
        )
        data["transactions"] = [
            Transaction.from_dict(t) for t in data.get("transactions", [])
        ]
        return data

    def get_address_info(self, address: str) -> dict:
        """Get address info (balance, nonce, tx counts)."""
        return self._get(f"/address/{address}")

    def get_balance(self, address: str) -> BalanceInfo:
        """Get balance for an address."""
        return BalanceInfo.from_dict(self._get(f"/balance/{address}"))

    def get_supply(self) -> SupplyInfo:
        """Get token supply information."""
        return SupplyInfo.from_dict(self._get("/supply"))

    def get_fee_info(self) -> FeeInfo:
        """Get current dynamic fee information."""
        return FeeInfo.from_dict(self._get("/fee"))

    def get_validators(self) -> list[str]:
        """Get list of registered validators."""
        data = self._get("/validators")
        return data.get("validators", [])

    def get_all_stakes(self) -> list[dict]:
        """Get all validator stakes."""
        data = self._get("/stakes")
        return data.get("validators", [])

    def get_validator_stake(self, validator: str) -> dict:
        """Get stake detail for a specific validator."""
        return self._get(f"/stakes/{validator}")

    def get_current_epoch(self) -> dict:
        """Get current epoch info."""
        return self._get("/epochs/current")

    def get_slashing_events(self, validator: str = "") -> list:
        """Get slashing events, optionally filtered by validator."""
        params = {}
        if validator:
            params["validator"] = validator
        return self._get("/slashing-events", params=params or None)

    def get_pool_summary(self) -> dict:
        """Get transaction pool summary."""
        return self._get("/pool")

    def get_pool_count(self) -> int:
        """Get transaction pool count."""
        data = self._get("/pool/count")
        return data.get("count", 0)

    def get_notarization(self, document_hash: str) -> dict:
        """Get notarization proof for a document hash."""
        return self._get(f"/notarizations/{document_hash}")

    def verify_document(self, document_hash: str) -> VerifyResult:
        """Verify a document hash exists on-chain."""
        data = self._post("/verify", {"document_hash": document_hash})
        return VerifyResult.from_dict(data)

    def get_state_proof(self, address: str, key: str = "balance") -> StateProof:
        """Get Merkle state proof for an address."""
        data = self._get(f"/state-proof/{address}", params={"key": key})
        return StateProof.from_dict(data)

    def get_state_root(self) -> str:
        """Get current state root hash."""
        data = self._get("/state-root")
        return data.get("state_root", "")

    def get_receipt(self, tx_id: str) -> Receipt:
        """Get transaction receipt."""
        data = self._get(f"/receipt/{tx_id}")
        return Receipt.from_dict(data)

    def get_events(
        self,
        event_type: str = "",
        sender: str = "",
        block_index: int | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Query blockchain events."""
        params: dict[str, str] = {}
        if event_type:
            params["type"] = event_type
        if sender:
            params["from"] = sender
        if block_index is not None:
            params["block_index"] = str(block_index)
        params["limit"] = str(limit)
        data = self._get("/events", params=params)
        return data.get("events", [])

    def get_finalized_height(self) -> int:
        """Get the finalized block height."""
        data = self._get("/finalized")
        return data.get("finalized_height", -1)

    # ------------------------------------------------------------------
    # Protected endpoints (require token)
    # ------------------------------------------------------------------

    def create_wallet(self) -> Wallet:
        """Create a new wallet."""
        return Wallet.from_dict(self._post("/wallets", {}, auth=True))

    def list_wallets(self) -> list[str]:
        """List wallet addresses."""
        data = self._get("/wallets", auth=True)
        return data.get("wallets", [])

    def transfer(
        self,
        from_wallet: str,
        to: str,
        amount: int,
        memo: str = "",
    ) -> str:
        """Transfer QBIT tokens. Returns tx_id."""
        data = self._post("/transfer", {
            "wallet_address": from_wallet,
            "to_address": to,
            "amount": amount,
            "memo": memo,
        }, auth=True)
        return data.get("tx_id", "")

    def notarize(
        self,
        wallet: str,
        document_hash: str,
        metadata: str = "",
    ) -> str:
        """Notarize a document hash. Returns tx_id."""
        data = self._post("/notarize", {
            "wallet_address": wallet,
            "document_hash": document_hash,
            "metadata": metadata,
        }, auth=True)
        return data.get("tx_id", "")

    def store(
        self,
        wallet: str,
        document_hash: str,
        cid: str = "",
        metadata: str = "",
    ) -> str:
        """Store a document reference on-chain. Returns tx_id."""
        data = self._post("/store", {
            "wallet_address": wallet,
            "document_hash": document_hash,
            "cid": cid,
            "metadata": metadata,
        }, auth=True)
        return data.get("tx_id", "")

    def share(
        self,
        wallet: str,
        recipient: str,
        cid: str = "",
        recipient_encryption_pk: str = "",
        expires: int = 0,
    ) -> str:
        """Share an encrypted document. Returns tx_id."""
        data = self._post("/share", {
            "wallet_address": wallet,
            "recipient_address": recipient,
            "cid": cid,
            "recipient_encryption_pk": recipient_encryption_pk,
            "expires": expires,
        }, auth=True)
        return data.get("tx_id", "")

    def stake(self, wallet: str, validator: str, amount: int) -> str:
        """Stake tokens on a validator. Returns tx_id."""
        data = self._post("/stake", {
            "wallet_address": wallet,
            "validator_address": validator,
            "amount": amount,
        }, auth=True)
        return data.get("tx_id", "")

    def delegate(self, wallet: str, validator: str, amount: int) -> str:
        """Delegate stake to a validator. Returns tx_id."""
        data = self._post("/delegate", {
            "wallet_address": wallet,
            "validator_address": validator,
            "amount": amount,
        }, auth=True)
        return data.get("tx_id", "")

    def unstake(self, wallet: str, validator: str, amount: int) -> str:
        """Begin unstaking from a validator. Returns tx_id."""
        data = self._post("/unstake", {
            "wallet_address": wallet,
            "validator_address": validator,
            "amount": amount,
        }, auth=True)
        return data.get("tx_id", "")

    def register_validator(self, wallet: str) -> dict:
        """Register wallet as validator. Returns dict with tx_id and validator_address."""
        return self._post("/register-validator", {
            "wallet_address": wallet,
        }, auth=True)

    def submit_evidence(self, **kwargs) -> dict:
        """Submit double-sign evidence for slashing."""
        return self._post("/evidence", kwargs, auth=True)

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    def register_webhook(
        self,
        url: str,
        events: list[str],
        secret: str,
    ) -> Webhook:
        """Register a webhook endpoint."""
        data = self._post("/webhooks", {
            "url": url,
            "events": events,
            "secret": secret,
        }, auth=True)
        return Webhook.from_dict(data)

    def list_webhooks(self) -> list[Webhook]:
        """List registered webhooks."""
        data = self._get("/webhooks", auth=True)
        return [Webhook.from_dict(w) for w in data.get("webhooks", [])]

    def delete_webhook(self, webhook_id: str) -> None:
        """Delete a webhook by ID."""
        self._delete(f"/webhooks/{webhook_id}", auth=True)
