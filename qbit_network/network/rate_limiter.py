"""Token bucket rate limiter for P2P and RPC DoS protection."""
import logging
import threading
import time
from collections import OrderedDict

logger = logging.getLogger("qbit_network.rate_limiter")

# Maximum tracked IPs before LRU eviction kicks in
_MAX_TRACKED = 10000

# Stale entry TTL — entries older than this are cleaned up
_STALE_TTL = 300  # 5 minutes


class _Bucket:
    """Single token bucket for one key (IP address)."""

    __slots__ = ('tokens', 'last_refill', 'violations')

    def __init__(self, burst: float):
        self.tokens = burst
        self.last_refill = time.monotonic()
        self.violations = 0


class RateLimiter:
    """Token bucket rate limiter with per-key tracking.

    Thread-safe (uses a lock around bucket state).  Works in both sync
    and async contexts because it never awaits — callers just call
    ``check(key)`` and act on the boolean result.

    Parameters
    ----------
    rate : float
        Tokens added per second (sustained rate).
    burst : float
        Maximum bucket capacity (allows short bursts above *rate*).
    """

    def __init__(self, rate: float, burst: float):
        self.rate = rate
        self.burst = burst
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()
        self._lock = threading.Lock()
        self._logged_keys: set[str] = set()  # avoid log-DoS per key

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _evict_one(self):
        """Evict one entry to make room for a new key.

        Prefers evicting clean entries (violations == 0) in LRU order.
        If all entries have violations, evicts the oldest violated entry.
        Must be called while self._lock is held.
        """
        # First pass: find oldest clean entry
        for k, b in self._buckets.items():
            if b.violations == 0:
                del self._buckets[k]
                self._logged_keys.discard(k)
                return
        # All entries have violations — evict the oldest (first in OrderedDict)
        evicted_key, _ = self._buckets.popitem(last=False)
        self._logged_keys.discard(evicted_key)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, key: str) -> bool:
        """Consume one token for *key*.  Returns True if allowed."""
        now = time.monotonic()

        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                # New entry — enforce LRU cap
                if len(self._buckets) >= _MAX_TRACKED:
                    self._evict_one()
                bucket = _Bucket(self.burst)
                self._buckets[key] = bucket
            else:
                # Move to end (most-recently-used)
                self._buckets.move_to_end(key)

            # Refill tokens
            elapsed = now - bucket.last_refill
            if elapsed > 0:
                bucket.tokens = min(
                    self.burst, bucket.tokens + elapsed * self.rate)
                bucket.last_refill = now

            # Try to consume
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True

            # Rate limited
            bucket.violations += 1
            if key not in self._logged_keys:
                self._logged_keys.add(key)
                logger.warning(f"Rate limiting {key}")
            return False

    def violations(self, key: str) -> int:
        """Return the violation count for *key*."""
        with self._lock:
            bucket = self._buckets.get(key)
            return bucket.violations if bucket else 0

    def reset(self, key: str) -> None:
        """Remove tracking for *key* (e.g. after disconnect)."""
        with self._lock:
            self._buckets.pop(key, None)
            self._logged_keys.discard(key)

    def cleanup(self) -> int:
        """Remove stale entries that have not been seen recently.

        Returns the number of entries removed.
        """
        now = time.monotonic()
        cutoff = now - _STALE_TTL
        removed = 0
        with self._lock:
            stale = [
                k for k, b in self._buckets.items()
                if b.last_refill < cutoff
            ]
            for k in stale:
                del self._buckets[k]
                self._logged_keys.discard(k)
                removed += 1
        return removed

    def __len__(self) -> int:
        return len(self._buckets)
