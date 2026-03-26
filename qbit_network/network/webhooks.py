"""Webhook registration, delivery, and lifecycle management."""
import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any

import aiohttp

logger = logging.getLogger("qbit_network.webhooks")

# Limits
MAX_WEBHOOKS = 100
MAX_URL_LENGTH = 2048
MAX_SECRET_LENGTH = 256
MAX_EVENTS_PER_HOOK = 20
DELIVERY_TIMEOUT = 10  # seconds per attempt
RETRY_DELAYS = [1, 5, 25]  # exponential backoff (seconds)
CONSECUTIVE_FAIL_DISABLE = 10  # disable after this many consecutive failures

# Valid event types that can be subscribed to
VALID_EVENT_TYPES = frozenset({
    "Transfer", "Notarize", "Store", "Share",
    "Stake", "Delegate", "Unstake",
    "KeyRegistered", "ValidatorRegistered", "KeyRevoked", "Slashed",
    "BlockReward", "EpochTransition",
})


class WebhookManager:
    """Manages webhook registration, storage, and async delivery."""

    def __init__(self):
        self._webhooks: dict[str, dict[str, Any]] = {}  # id -> webhook dict
        self._delivery_tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, url: str, events: list[str], secret: str) -> dict:
        """Register a new webhook. Returns the webhook dict.

        Raises ValueError on invalid input or limit exceeded.
        """
        # Validate URL
        if not isinstance(url, str) or not url:
            raise ValueError("url must be a non-empty string")
        if len(url) > MAX_URL_LENGTH:
            raise ValueError(f"url exceeds {MAX_URL_LENGTH} characters")
        if not url.startswith("http://") and not url.startswith("https://"):
            raise ValueError("url must start with http:// or https://")

        # Validate events
        if not isinstance(events, list) or not events:
            raise ValueError("events must be a non-empty list")
        if len(events) > MAX_EVENTS_PER_HOOK:
            raise ValueError(f"max {MAX_EVENTS_PER_HOOK} events per webhook")
        for ev in events:
            if not isinstance(ev, str):
                raise ValueError("each event must be a string")
            if ev not in VALID_EVENT_TYPES:
                raise ValueError(
                    f"unknown event type: {ev}. "
                    f"Valid types: {sorted(VALID_EVENT_TYPES)}"
                )

        # Validate secret
        if not isinstance(secret, str) or not secret:
            raise ValueError("secret must be a non-empty string")
        if len(secret) > MAX_SECRET_LENGTH:
            raise ValueError(f"secret exceeds {MAX_SECRET_LENGTH} characters")

        # Limit check
        active = sum(1 for w in self._webhooks.values()
                     if w["status"] != "deleted")
        if active >= MAX_WEBHOOKS:
            raise ValueError(f"max {MAX_WEBHOOKS} webhooks reached")

        webhook_id = secrets.token_hex(16)
        webhook = {
            "id": webhook_id,
            "url": url,
            "events": list(events),
            "secret": secret,
            "created_at": time.time(),
            "status": "active",
            "consecutive_failures": 0,
        }
        self._webhooks[webhook_id] = webhook
        return _safe_webhook(webhook)

    def list_webhooks(self) -> list[dict]:
        """List all non-deleted webhooks (secrets excluded)."""
        return [
            _safe_webhook(w)
            for w in self._webhooks.values()
            if w["status"] != "deleted"
        ]

    def delete(self, webhook_id: str) -> bool:
        """Delete a webhook by ID. Returns True if found and deleted."""
        if not isinstance(webhook_id, str):
            raise ValueError("webhook_id must be a string")
        webhook = self._webhooks.get(webhook_id)
        if not webhook or webhook["status"] == "deleted":
            return False
        webhook["status"] = "deleted"
        return True

    def get(self, webhook_id: str) -> dict | None:
        """Get a webhook by ID (safe copy)."""
        webhook = self._webhooks.get(webhook_id)
        if not webhook or webhook["status"] == "deleted":
            return None
        return _safe_webhook(webhook)

    # ------------------------------------------------------------------
    # Delivery
    # ------------------------------------------------------------------

    async def deliver(self, events: list[dict], block_index: int) -> None:
        """Deliver events to all matching webhooks.

        Parameters
        ----------
        events : list[dict]
            List of event dicts, each with "type" and "data" keys.
        block_index : int
            The block index these events came from.
        """
        if not events:
            return

        for webhook in list(self._webhooks.values()):
            if webhook["status"] not in ("active", "failing"):
                continue

            # Filter events that match this webhook's subscriptions
            matching = [
                e for e in events
                if e.get("type") in webhook["events"]
            ]
            if not matching:
                continue

            # Deliver each matching event
            for event in matching:
                task = asyncio.create_task(
                    self._deliver_one(webhook, event, block_index)
                )
                self._delivery_tasks.append(task)
                task.add_done_callback(lambda t: self._delivery_tasks.remove(t)
                                       if t in self._delivery_tasks else None)

    async def _deliver_one(
        self, webhook: dict, event: dict, block_index: int
    ) -> bool:
        """Attempt to deliver a single event to a webhook with retries.

        Returns True if delivery succeeded, False otherwise.
        """
        payload = json.dumps({
            "event": event,
            "block_index": block_index,
            "timestamp": time.time(),
        })

        signature = hmac.new(
            webhook["secret"].encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()

        headers = {
            "Content-Type": "application/json",
            "X-QBit-Signature": signature,
            "X-QBit-Webhook-Id": webhook["id"],
        }

        for attempt, delay in enumerate(RETRY_DELAYS):
            if webhook["status"] == "deleted":
                return False

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        webhook["url"],
                        data=payload,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=DELIVERY_TIMEOUT),
                    ) as resp:
                        if 200 <= resp.status < 300:
                            webhook["consecutive_failures"] = 0
                            if webhook["status"] == "failing":
                                webhook["status"] = "active"
                            return True

                        logger.debug(
                            f"Webhook {webhook['id'][:8]}... "
                            f"delivery attempt {attempt + 1} failed: "
                            f"HTTP {resp.status}"
                        )
            except Exception as e:
                logger.debug(
                    f"Webhook {webhook['id'][:8]}... "
                    f"delivery attempt {attempt + 1} error: {e}"
                )

            # Wait before retrying (except after last attempt)
            if attempt < len(RETRY_DELAYS) - 1:
                await asyncio.sleep(delay)

        # All retries exhausted
        webhook["consecutive_failures"] += 1
        if webhook["consecutive_failures"] >= CONSECUTIVE_FAIL_DISABLE:
            webhook["status"] = "disabled"
            logger.warning(
                f"Webhook {webhook['id'][:8]}... disabled after "
                f"{CONSECUTIVE_FAIL_DISABLE} consecutive failures"
            )
        elif webhook["status"] == "active":
            webhook["status"] = "failing"
        return False

    async def stop(self) -> None:
        """Cancel all pending delivery tasks."""
        for task in list(self._delivery_tasks):
            if not task.done():
                task.cancel()
        self._delivery_tasks.clear()


def _safe_webhook(webhook: dict) -> dict:
    """Return a webhook dict without the secret (for API responses)."""
    return {
        "id": webhook["id"],
        "url": webhook["url"],
        "events": webhook["events"],
        "created_at": webhook["created_at"],
        "status": webhook["status"],
        "consecutive_failures": webhook["consecutive_failures"],
    }


def compute_webhook_signature(secret: str, payload: str) -> str:
    """Compute HMAC-SHA256 signature for a webhook payload.

    Useful for verifying webhook deliveries on the receiving end.
    """
    return hmac.new(
        secret.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()
