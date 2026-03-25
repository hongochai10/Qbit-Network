"""Tests for TLS certificate management (tls_manager.py)."""
import asyncio
import datetime
import ipaddress
import os
import ssl
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qbit_network.network.tls_manager import TLSManager, _atomic_write, _parse_cert_expiry
from qbit_network.config import TLS_CERT_VALIDITY_DAYS, TLS_RENEWAL_THRESHOLD_DAYS


# =========================================================================
# TLSManager — certificate generation
# =========================================================================


class TestTLSManagerGeneration:
    """Test certificate generation and file management."""

    def test_ensure_certificate_creates_files(self, tmp_path):
        mgr = TLSManager(data_dir=str(tmp_path), hostname="localhost")
        cert, key = mgr.ensure_certificate()
        assert os.path.isfile(cert)
        assert os.path.isfile(key)
        assert cert.endswith("node.crt")
        assert key.endswith("node.key")

    def test_ensure_certificate_idempotent(self, tmp_path):
        mgr = TLSManager(data_dir=str(tmp_path), hostname="localhost")
        cert1, key1 = mgr.ensure_certificate()
        # Read the cert content
        with open(cert1, "rb") as f:
            content1 = f.read()
        # Second call should not regenerate
        cert2, key2 = mgr.ensure_certificate()
        with open(cert2, "rb") as f:
            content2 = f.read()
        assert content1 == content2

    def test_key_file_permissions(self, tmp_path):
        mgr = TLSManager(data_dir=str(tmp_path), hostname="localhost")
        _, key = mgr.ensure_certificate()
        mode = os.stat(key).st_mode & 0o777
        assert mode == 0o600

    def test_cert_contains_hostname_cn(self, tmp_path):
        from cryptography import x509
        from cryptography.x509.oid import NameOID

        mgr = TLSManager(data_dir=str(tmp_path), hostname="mynode.example.com")
        cert_path, _ = mgr.ensure_certificate()
        with open(cert_path, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
        cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
        assert cn == "mynode.example.com"

    def test_cert_san_includes_hostname_and_localhost(self, tmp_path):
        from cryptography import x509

        mgr = TLSManager(data_dir=str(tmp_path), hostname="mynode.local")
        cert_path, _ = mgr.ensure_certificate()
        with open(cert_path, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
        san = cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value
        dns_names = san.get_values_for_type(x509.DNSName)
        assert "mynode.local" in dns_names
        assert "localhost" in dns_names
        ips = san.get_values_for_type(x509.IPAddress)
        assert ipaddress.IPv4Address("127.0.0.1") in ips

    def test_cert_san_ip_hostname(self, tmp_path):
        """When hostname is an IP, it appears as IPAddress SAN."""
        from cryptography import x509

        mgr = TLSManager(data_dir=str(tmp_path), hostname="192.168.1.100")
        cert_path, _ = mgr.ensure_certificate()
        with open(cert_path, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
        san = cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value
        ips = san.get_values_for_type(x509.IPAddress)
        assert ipaddress.IPv4Address("192.168.1.100") in ips

    def test_cert_validity_period(self, tmp_path):
        from cryptography import x509

        days = 90
        mgr = TLSManager(data_dir=str(tmp_path), hostname="localhost", validity_days=days)
        cert_path, _ = mgr.ensure_certificate()
        with open(cert_path, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
        delta = cert.not_valid_after_utc - cert.not_valid_before_utc
        # Allow 1-second tolerance
        assert abs(delta.days - days) <= 1

    def test_cert_has_basic_constraints(self, tmp_path):
        from cryptography import x509

        mgr = TLSManager(data_dir=str(tmp_path), hostname="localhost")
        cert_path, _ = mgr.ensure_certificate()
        with open(cert_path, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
        bc = cert.extensions.get_extension_for_class(x509.BasicConstraints)
        assert bc.critical is True
        assert bc.value.ca is False


# =========================================================================
# TLSManager — expiry checking
# =========================================================================


class TestTLSManagerExpiry:
    """Test certificate expiry detection and renewal."""

    def test_days_until_expiry_positive(self, tmp_path):
        mgr = TLSManager(data_dir=str(tmp_path), hostname="localhost", validity_days=365)
        mgr.ensure_certificate()
        days = mgr.days_until_expiry()
        assert days is not None
        assert 360 <= days <= 366

    def test_days_until_expiry_none_when_no_cert(self, tmp_path):
        mgr = TLSManager(data_dir=str(tmp_path), hostname="localhost")
        assert mgr.days_until_expiry() is None

    def test_cert_needs_renewal_expired(self, tmp_path):
        """A cert with validity_days=0 is immediately near expiry."""
        # Generate with very short validity
        mgr = TLSManager(
            data_dir=str(tmp_path), hostname="localhost",
            validity_days=1, renewal_threshold_days=5,
        )
        mgr.ensure_certificate()
        # The cert has 1 day validity, threshold is 5 -> needs renewal
        assert mgr._cert_needs_renewal() is True

    def test_check_and_renew_when_needed(self, tmp_path):
        mgr = TLSManager(
            data_dir=str(tmp_path), hostname="localhost",
            validity_days=1, renewal_threshold_days=5,
        )
        mgr.ensure_certificate()
        # Force SSL context creation
        mgr.get_ssl_context()
        # Should renew
        assert mgr.check_and_renew() is True

    def test_check_and_renew_not_needed(self, tmp_path):
        mgr = TLSManager(
            data_dir=str(tmp_path), hostname="localhost",
            validity_days=365, renewal_threshold_days=30,
        )
        mgr.ensure_certificate()
        mgr.get_ssl_context()
        assert mgr.check_and_renew() is False


# =========================================================================
# TLSManager — SSL context
# =========================================================================


class TestTLSManagerSSLContext:
    """Test SSL context creation and hot-reload."""

    def test_get_ssl_context_returns_valid(self, tmp_path):
        mgr = TLSManager(data_dir=str(tmp_path), hostname="localhost")
        mgr.ensure_certificate()
        ctx = mgr.get_ssl_context()
        assert isinstance(ctx, ssl.SSLContext)

    def test_get_ssl_context_cached(self, tmp_path):
        mgr = TLSManager(data_dir=str(tmp_path), hostname="localhost")
        mgr.ensure_certificate()
        ctx1 = mgr.get_ssl_context()
        ctx2 = mgr.get_ssl_context()
        assert ctx1 is ctx2

    def test_reload_ssl_context(self, tmp_path):
        mgr = TLSManager(data_dir=str(tmp_path), hostname="localhost")
        mgr.ensure_certificate()
        ctx1 = mgr.get_ssl_context()
        ctx2 = mgr.reload_ssl_context()
        assert ctx1 is not ctx2
        assert isinstance(ctx2, ssl.SSLContext)

    def test_cert_files_changed_detection(self, tmp_path):
        mgr = TLSManager(data_dir=str(tmp_path), hostname="localhost")
        mgr.ensure_certificate()
        mgr.get_ssl_context()  # loads and records mtimes
        assert mgr.cert_files_changed() is False

        # Simulate file change by touching cert
        time.sleep(0.05)
        os.utime(mgr.cert_path, None)
        assert mgr.cert_files_changed() is True


# =========================================================================
# TLSManager — watcher
# =========================================================================


class TestTLSManagerWatcher:
    """Test async watcher start/stop."""

    @pytest.mark.asyncio
    async def test_start_and_stop_watcher(self, tmp_path):
        mgr = TLSManager(data_dir=str(tmp_path), hostname="localhost")
        mgr.ensure_certificate()
        mgr.get_ssl_context()
        await mgr.start_watcher(check_interval=0.1)
        assert mgr._watcher_task is not None
        assert not mgr._watcher_task.done()
        await mgr.stop_watcher()
        assert mgr._watcher_task.done()

    @pytest.mark.asyncio
    async def test_stop_watcher_when_not_started(self, tmp_path):
        mgr = TLSManager(data_dir=str(tmp_path), hostname="localhost")
        # Should not raise
        await mgr.stop_watcher()


# =========================================================================
# TLSManager — input validation
# =========================================================================


class TestTLSManagerValidation:
    """Test constructor input validation."""

    def test_invalid_data_dir_type(self):
        with pytest.raises(TypeError, match="data_dir must be a string"):
            TLSManager(data_dir=123)

    def test_invalid_hostname_empty(self):
        with pytest.raises(TypeError, match="non-empty string"):
            TLSManager(data_dir="/tmp", hostname="")

    def test_invalid_hostname_type(self):
        with pytest.raises(TypeError, match="non-empty string"):
            TLSManager(data_dir="/tmp", hostname=None)

    def test_invalid_validity_days_zero(self):
        with pytest.raises(ValueError, match="positive integer"):
            TLSManager(data_dir="/tmp", validity_days=0)

    def test_invalid_validity_days_type(self):
        with pytest.raises(ValueError, match="positive integer"):
            TLSManager(data_dir="/tmp", validity_days="365")

    def test_invalid_renewal_threshold_negative(self):
        with pytest.raises(ValueError, match="non-negative integer"):
            TLSManager(data_dir="/tmp", renewal_threshold_days=-1)


# =========================================================================
# Helper functions
# =========================================================================


class TestAtomicWrite:
    """Test the _atomic_write helper."""

    def test_writes_content(self, tmp_path):
        path = str(tmp_path / "test.pem")
        _atomic_write(path, b"hello TLS")
        with open(path, "rb") as f:
            assert f.read() == b"hello TLS"

    def test_sets_permissions(self, tmp_path):
        path = str(tmp_path / "key.pem")
        _atomic_write(path, b"secret key", mode=0o600)
        mode = os.stat(path).st_mode & 0o777
        assert mode == 0o600

    def test_atomic_replaces_existing(self, tmp_path):
        path = str(tmp_path / "cert.pem")
        _atomic_write(path, b"old content")
        _atomic_write(path, b"new content")
        with open(path, "rb") as f:
            assert f.read() == b"new content"


class TestParseCertExpiry:
    """Test _parse_cert_expiry helper."""

    def test_returns_datetime_for_valid_cert(self, tmp_path):
        mgr = TLSManager(data_dir=str(tmp_path), hostname="localhost", validity_days=100)
        mgr.ensure_certificate()
        expiry = _parse_cert_expiry(mgr.cert_path)
        assert isinstance(expiry, datetime.datetime)
        assert expiry.tzinfo is not None  # timezone-aware

    def test_returns_none_for_missing_file(self, tmp_path):
        assert _parse_cert_expiry(str(tmp_path / "nonexistent.pem")) is None

    def test_returns_none_for_invalid_file(self, tmp_path):
        bad = tmp_path / "bad.pem"
        bad.write_text("not a certificate")
        assert _parse_cert_expiry(str(bad)) is None


# =========================================================================
# Config constants
# =========================================================================


class TestTLSConfig:
    """Verify TLS config constants exist and are reasonable."""

    def test_validity_days(self):
        assert TLS_CERT_VALIDITY_DAYS == 365

    def test_renewal_threshold(self):
        assert TLS_RENEWAL_THRESHOLD_DAYS == 30

    def test_threshold_less_than_validity(self):
        assert TLS_RENEWAL_THRESHOLD_DAYS < TLS_CERT_VALIDITY_DAYS
