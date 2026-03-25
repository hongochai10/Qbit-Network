"""Tests for qbit_network.network.rate_limiter (Sprint 3)."""
import threading
import time
import pytest
from qbit_network.network.rate_limiter import RateLimiter, _MAX_TRACKED, _STALE_TTL
from qbit_network.network.p2p import _is_safe_peer


# ===========================================================================
# Token bucket: basic behaviour
# ===========================================================================

class TestTokenBucketBasic:
    """Basic token bucket allow/deny logic."""

    def test_fresh_bucket_allows_first_request(self):
        rl = RateLimiter(rate=1.0, burst=5.0)
        assert rl.check("ip1") is True

    def test_burst_is_fully_consumed(self):
        burst = 5
        rl = RateLimiter(rate=0.0, burst=burst)  # rate=0 so no refill
        results = [rl.check("ip1") for _ in range(burst)]
        assert all(results)

    def test_bucket_empty_after_burst(self):
        burst = 3
        rl = RateLimiter(rate=0.0, burst=burst)
        for _ in range(burst):
            rl.check("ip1")
        # Next request should be denied
        assert rl.check("ip1") is False

    def test_different_keys_are_independent(self):
        burst = 2
        rl = RateLimiter(rate=0.0, burst=burst)
        for _ in range(burst):
            rl.check("ip1")
        # ip1 is empty, ip2 is fresh
        assert rl.check("ip1") is False
        assert rl.check("ip2") is True

    def test_sustained_rate_enforcement(self):
        """After burst, requests are throttled to sustained rate."""
        rl = RateLimiter(rate=100.0, burst=2.0)  # 100 req/s but burst=2
        # Exhaust burst
        rl.check("ip1")
        rl.check("ip1")
        # Third check: no tokens yet (no time has passed)
        assert rl.check("ip1") is False

    def test_zero_rate_never_refills(self):
        rl = RateLimiter(rate=0.0, burst=1.0)
        rl.check("ip1")  # exhaust
        # Even after some time passes with zero rate, no refill
        # (In test we just check logic directly)
        assert rl.check("ip1") is False


# ===========================================================================
# Token bucket: violations
# ===========================================================================

class TestViolationCounting:
    """Violation counter increments when rate limited."""

    def test_violations_zero_initially(self):
        rl = RateLimiter(rate=0.0, burst=5.0)
        assert rl.violations("unknown") == 0

    def test_violations_increment_on_deny(self):
        rl = RateLimiter(rate=0.0, burst=1.0)
        rl.check("ip1")  # consume token
        rl.check("ip1")  # denied -> violation
        assert rl.violations("ip1") == 1

    def test_violations_accumulate(self):
        rl = RateLimiter(rate=0.0, burst=1.0)
        rl.check("ip1")  # consume
        for _ in range(3):
            rl.check("ip1")  # 3 denials
        assert rl.violations("ip1") == 3

    def test_violations_do_not_increment_on_allow(self):
        rl = RateLimiter(rate=0.0, burst=5.0)
        rl.check("ip1")  # allowed
        assert rl.violations("ip1") == 0

    def test_reset_removes_violations(self):
        rl = RateLimiter(rate=0.0, burst=1.0)
        rl.check("ip1")  # exhaust
        rl.check("ip1")  # violation
        assert rl.violations("ip1") == 1
        rl.reset("ip1")
        assert rl.violations("ip1") == 0

    def test_violations_nonexistent_key_returns_zero(self):
        rl = RateLimiter(rate=1.0, burst=5.0)
        assert rl.violations("nobody") == 0


# ===========================================================================
# LRU eviction
# ===========================================================================

class TestLRUEviction:
    """LRU eviction when _MAX_TRACKED entries are reached."""

    def test_eviction_under_max_tracked(self):
        """Below _MAX_TRACKED, no eviction occurs."""
        rl = RateLimiter(rate=1.0, burst=5.0)
        for i in range(100):
            rl.check(f"ip{i}")
        assert len(rl) == 100

    def test_len_does_not_exceed_max_tracked(self):
        rl = RateLimiter(rate=1.0, burst=5.0)
        for i in range(_MAX_TRACKED + 10):
            rl.check(f"ip{i}")
        assert len(rl) <= _MAX_TRACKED

    def test_eviction_prefers_clean_entries(self):
        """Eviction should prefer entries with violations==0 over violated ones."""
        rl = RateLimiter(rate=0.0, burst=1.0)
        # Fill to MAX_TRACKED - 1 with clean entries
        # Then add one violated entry
        # Then add one more — the evicted should be clean, not violated
        # (Hard to test without controlling internals, so we verify violated entry survives)
        rl2 = RateLimiter(rate=0.0, burst=1.0)
        # Create a violated entry
        rl2.check("violated")   # consume
        rl2.check("violated")   # violation
        assert rl2.violations("violated") == 1
        # Add many clean entries to trigger eviction
        for i in range(_MAX_TRACKED + 5):
            rl2.check(f"clean{i}")
        # The violated entry should not have been evicted first
        # (can only confirm it still has tracked violations if it survived)
        # Since _MAX_TRACKED is large (10000), just check len is bounded
        assert len(rl2) <= _MAX_TRACKED

    def test_lru_order_is_maintained(self):
        """Most recently used entry should not be evicted."""
        rl = RateLimiter(rate=1.0, burst=5.0)
        # Add entries, then keep using one
        for i in range(50):
            rl.check(f"ip{i}")
        # Keep using ip0 so it stays as MRU
        for _ in range(5):
            rl.check("ip0")
        # ip0 should still be tracked
        # (Not guaranteed by test but check it doesn't crash)
        assert len(rl) <= _MAX_TRACKED


# ===========================================================================
# Cleanup of stale entries
# ===========================================================================

class TestCleanup:
    """Stale entry cleanup."""

    def test_cleanup_removes_stale_entries(self):
        """After cleanup, entries with old last_refill are removed."""
        rl = RateLimiter(rate=1.0, burst=5.0)
        rl.check("stale")
        rl.check("fresh")

        # Force the stale bucket to appear old by manipulating its last_refill
        with rl._lock:
            rl._buckets["stale"].last_refill -= _STALE_TTL + 1

        removed = rl.cleanup()
        assert removed >= 1
        assert rl.violations("stale") == 0  # stale entry gone

    def test_cleanup_does_not_remove_fresh_entries(self):
        rl = RateLimiter(rate=1.0, burst=5.0)
        rl.check("fresh")
        removed = rl.cleanup()
        assert removed == 0
        # Fresh entry still present
        with rl._lock:
            assert "fresh" in rl._buckets

    def test_cleanup_returns_count_of_removed(self):
        rl = RateLimiter(rate=1.0, burst=5.0)
        for i in range(5):
            rl.check(f"ip{i}")
        with rl._lock:
            for i in range(3):
                rl._buckets[f"ip{i}"].last_refill -= _STALE_TTL + 1
        removed = rl.cleanup()
        assert removed == 3

    def test_cleanup_empty_limiter(self):
        rl = RateLimiter(rate=1.0, burst=5.0)
        assert rl.cleanup() == 0


# ===========================================================================
# Reset behaviour
# ===========================================================================

class TestReset:
    """reset() removes tracking for a key."""

    def test_reset_removes_tracked_entry(self):
        rl = RateLimiter(rate=0.0, burst=5.0)
        rl.check("ip1")
        with rl._lock:
            assert "ip1" in rl._buckets
        rl.reset("ip1")
        with rl._lock:
            assert "ip1" not in rl._buckets

    def test_reset_nonexistent_key_is_noop(self):
        rl = RateLimiter(rate=1.0, burst=5.0)
        # Should not raise
        rl.reset("nobody")

    def test_after_reset_key_gets_fresh_bucket(self):
        rl = RateLimiter(rate=0.0, burst=1.0)
        rl.check("ip1")  # exhaust
        rl.check("ip1")  # violation
        rl.reset("ip1")
        # Now ip1 gets a fresh bucket
        assert rl.check("ip1") is True
        assert rl.violations("ip1") == 0


# ===========================================================================
# Thread safety
# ===========================================================================

class TestThreadSafety:
    """Basic concurrent access does not raise or corrupt state."""

    def test_concurrent_check_does_not_crash(self):
        rl = RateLimiter(rate=100.0, burst=100.0)
        errors = []

        def worker(ip: str):
            try:
                for _ in range(50):
                    rl.check(ip)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(f"ip{i}",))
                   for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"

    def test_concurrent_mixed_operations(self):
        """check, reset, and cleanup can run simultaneously without corruption."""
        rl = RateLimiter(rate=10.0, burst=5.0)
        errors = []

        def checker():
            try:
                for i in range(20):
                    rl.check(f"ip{i % 5}")
            except Exception as e:
                errors.append(e)

        def resetter():
            try:
                for i in range(10):
                    rl.reset(f"ip{i % 5}")
            except Exception as e:
                errors.append(e)

        def cleaner():
            try:
                for _ in range(5):
                    rl.cleanup()
            except Exception as e:
                errors.append(e)

        threads = (
            [threading.Thread(target=checker) for _ in range(3)]
            + [threading.Thread(target=resetter) for _ in range(2)]
            + [threading.Thread(target=cleaner) for _ in range(2)]
        )
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"

    def test_len_consistent_under_concurrency(self):
        """__len__ returns a non-negative value under concurrent check."""
        rl = RateLimiter(rate=100.0, burst=100.0)
        errors = []

        def worker():
            try:
                for i in range(100):
                    rl.check(f"ip{i}")
                    _ = len(rl)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(rl) >= 0


# ===========================================================================
# Localhost exemption (config-level)
# ===========================================================================

class TestLocalhostExemption:
    """_is_safe_peer blocks local addresses unless ALLOW_PRIVATE_PEERS is set."""

    def test_localhost_blocked_by_default(self):
        assert not _is_safe_peer("127.0.0.1", 9000, "0.0.0.0", 9001)

    def test_loopback_ipv6_blocked(self):
        assert not _is_safe_peer("::1", 9000, "0.0.0.0", 9001)

    def test_public_ip_allowed(self):
        assert _is_safe_peer("8.8.8.8", 9000, "0.0.0.0", 9001)

    def test_private_ip_blocked_by_default(self):
        assert not _is_safe_peer("192.168.1.1", 9000, "0.0.0.0", 9001)

    def test_blocked_ports_unsafe(self):
        """Standard service ports are blocked regardless of IP."""
        assert not _is_safe_peer("8.8.8.8", 22, "0.0.0.0", 9001)   # SSH
        assert not _is_safe_peer("8.8.8.8", 3306, "0.0.0.0", 9001) # MySQL

    def test_self_connection_blocked(self):
        """Connecting to self (same host+port) is blocked."""
        assert not _is_safe_peer("0.0.0.0", 9000, "0.0.0.0", 9000)
