"""TLS certificate management: auto-generation, validation, renewal, and hot-reload."""
import asyncio
import datetime
import ipaddress
import logging
import os
import signal
import ssl
import tempfile
import threading
from typing import Optional

logger = logging.getLogger("qbit_network.tls_manager")

# Defaults (can be overridden via config)
_DEFAULT_VALIDITY_DAYS = 365
_DEFAULT_RENEWAL_THRESHOLD_DAYS = 30


class TLSManager:
    """Manages TLS certificates for the node.

    Supports three modes:
    1. Auto-managed self-signed certs (--tls-auto): generates, stores, and
       auto-renews a self-signed certificate in <data_dir>/tls/.
    2. External certs (--tls-cert/--tls-key): watches files for changes,
       reloads SSL context when modified.
    3. Backward-compat self-signed (--tls-self-signed): alias for --tls-auto.
    """

    def __init__(
        self,
        data_dir: str,
        hostname: str = "localhost",
        validity_days: int = _DEFAULT_VALIDITY_DAYS,
        renewal_threshold_days: int = _DEFAULT_RENEWAL_THRESHOLD_DAYS,
    ):
        if not isinstance(data_dir, str):
            raise TypeError("data_dir must be a string")
        if not isinstance(hostname, str) or not hostname:
            raise TypeError("hostname must be a non-empty string")
        if not isinstance(validity_days, int) or validity_days < 1:
            raise ValueError("validity_days must be a positive integer")
        if not isinstance(renewal_threshold_days, int) or renewal_threshold_days < 0:
            raise ValueError("renewal_threshold_days must be a non-negative integer")

        self._data_dir = data_dir
        self._hostname = hostname
        self._validity_days = validity_days
        self._renewal_threshold_days = renewal_threshold_days

        tls_dir = os.path.join(data_dir, "tls") if data_dir else os.path.join(
            tempfile.gettempdir(), "qbit_tls"
        )
        self._tls_dir = tls_dir
        self.cert_path = os.path.join(tls_dir, "node.crt")
        self.key_path = os.path.join(tls_dir, "node.key")

        # For hot-reload tracking
        self._cert_mtime: float = 0.0
        self._key_mtime: float = 0.0
        self._ssl_context: Optional[ssl.SSLContext] = None
        self._lock = threading.Lock()
        self._watcher_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ensure_certificate(self) -> tuple[str, str]:
        """Return (cert_path, key_path), generating or renewing if needed.

        This is the main entry point for --tls-auto mode.
        """
        os.makedirs(self._tls_dir, exist_ok=True)

        if self._cert_needs_renewal():
            logger.info("Generating new self-signed TLS certificate")
            self._generate_self_signed()
        else:
            logger.info("Existing TLS certificate is still valid")

        return self.cert_path, self.key_path

    def get_ssl_context(self) -> ssl.SSLContext:
        """Create or return cached SSL context for the managed certificate."""
        with self._lock:
            if self._ssl_context is None:
                self._ssl_context = self._build_ssl_context(
                    self.cert_path, self.key_path
                )
                self._update_mtimes()
            return self._ssl_context

    def get_cert_expiry(self) -> Optional[datetime.datetime]:
        """Return the certificate's expiry date, or None if unreadable."""
        return _parse_cert_expiry(self.cert_path)

    def days_until_expiry(self) -> Optional[int]:
        """Return days until cert expires, or None if cert is unreadable."""
        expiry = self.get_cert_expiry()
        if expiry is None:
            return None
        delta = expiry - datetime.datetime.now(datetime.timezone.utc)
        return delta.days

    def reload_ssl_context(self) -> ssl.SSLContext:
        """Force-reload the SSL context from disk."""
        with self._lock:
            self._ssl_context = self._build_ssl_context(
                self.cert_path, self.key_path
            )
            self._update_mtimes()
            logger.info("TLS SSL context reloaded")
            return self._ssl_context

    def check_and_renew(self) -> bool:
        """Check if auto-managed cert needs renewal; regenerate if so.

        Returns True if cert was renewed.
        """
        if self._cert_needs_renewal():
            logger.info("Auto-renewing TLS certificate")
            self._generate_self_signed()
            self.reload_ssl_context()
            return True
        return False

    def cert_files_changed(self) -> bool:
        """Check if cert/key files have been modified since last load."""
        try:
            cert_mt = os.path.getmtime(self.cert_path)
            key_mt = os.path.getmtime(self.key_path)
            return cert_mt != self._cert_mtime or key_mt != self._key_mtime
        except OSError:
            return False

    async def start_watcher(self, check_interval: float = 60.0):
        """Start background task that watches for cert changes and expiry."""
        self._watcher_task = asyncio.create_task(
            self._watch_loop(check_interval)
        )

    async def stop_watcher(self):
        """Stop the background watcher task."""
        if self._watcher_task and not self._watcher_task.done():
            self._watcher_task.cancel()
            try:
                await self._watcher_task
            except asyncio.CancelledError:
                pass

    def install_sighup_handler(self):
        """Install SIGHUP handler to reload TLS context (Unix only)."""
        try:
            loop = asyncio.get_running_loop()
            loop.add_signal_handler(signal.SIGHUP, self._on_sighup)
            logger.info("SIGHUP handler installed for TLS cert reload")
        except (NotImplementedError, OSError, RuntimeError):
            # Windows or no running loop
            logger.debug("SIGHUP handler not available on this platform")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _cert_needs_renewal(self) -> bool:
        """Return True if cert doesn't exist, is unreadable, or near expiry."""
        if not os.path.isfile(self.cert_path) or not os.path.isfile(self.key_path):
            return True

        days = self.days_until_expiry()
        if days is None:
            return True  # Can't parse -> regenerate
        if days < 0:
            logger.warning("TLS certificate has expired")
            return True
        if days <= self._renewal_threshold_days:
            logger.warning(
                f"TLS certificate expires in {days} days "
                f"(threshold: {self._renewal_threshold_days})"
            )
            return True
        return False

    def _generate_self_signed(self):
        """Generate a self-signed certificate with proper X.509 fields."""
        try:
            from cryptography import x509
            from cryptography.x509.oid import NameOID
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.serialization import PrivateFormat
            from cryptography.hazmat.primitives.asymmetric import ec
        except ImportError:
            raise RuntimeError(
                "cryptography package required for TLS certificate generation. "
                "Install with: pip install cryptography"
            )

        key = ec.generate_private_key(ec.SECP256R1())

        name = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, self._hostname),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "QBit Network Node"),
        ])

        # Build SAN list
        san_entries: list = [x509.DNSName(self._hostname)]
        if self._hostname != "localhost":
            san_entries.append(x509.DNSName("localhost"))

        # Add IPs
        san_entries.append(x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")))
        san_entries.append(x509.IPAddress(ipaddress.IPv6Address("::1")))

        # If hostname looks like an IP, add it as IPAddress SAN
        try:
            ip = ipaddress.ip_address(self._hostname)
            san_entries.append(x509.IPAddress(ip))
        except ValueError:
            pass

        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=self._validity_days))
            .add_extension(
                x509.SubjectAlternativeName(san_entries),
                critical=False,
            )
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None),
                critical=True,
            )
            .sign(key, hashes.SHA256())
        )

        os.makedirs(self._tls_dir, exist_ok=True)

        # Atomic write for cert
        cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        _atomic_write(self.cert_path, cert_pem)

        # Atomic write for key
        key_pem = key.private_bytes(
            serialization.Encoding.PEM,
            PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
        _atomic_write(self.key_path, key_pem, mode=0o600)

        logger.info(
            f"Generated self-signed TLS cert: CN={self._hostname}, "
            f"valid for {self._validity_days} days"
        )

    def _build_ssl_context(
        self, cert_path: str, key_path: str
    ) -> ssl.SSLContext:
        """Build a TLS server context with strong defaults."""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(cert_path, key_path)
        return ctx

    def _update_mtimes(self):
        """Record current modification times for cert and key files."""
        try:
            self._cert_mtime = os.path.getmtime(self.cert_path)
            self._key_mtime = os.path.getmtime(self.key_path)
        except OSError:
            self._cert_mtime = 0.0
            self._key_mtime = 0.0

    async def _watch_loop(self, interval: float):
        """Background loop: check for cert changes and approaching expiry."""
        try:
            while True:
                await asyncio.sleep(interval)

                # Check file modifications (external cert replacement)
                if self.cert_files_changed():
                    logger.info("TLS certificate files changed on disk, reloading")
                    self.reload_ssl_context()

                # Check expiry for auto-managed certs
                days = self.days_until_expiry()
                if days is not None:
                    if days <= self._renewal_threshold_days:
                        logger.warning(
                            f"TLS certificate expires in {days} days"
                        )
        except asyncio.CancelledError:
            pass

    def _on_sighup(self):
        """Handle SIGHUP by reloading TLS context."""
        logger.info("Received SIGHUP, reloading TLS certificate")
        self.reload_ssl_context()


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _atomic_write(path: str, data: bytes, mode: int = 0o644):
    """Write data to path atomically via tempfile + os.replace."""
    dir_path = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(dir=dir_path)
    try:
        os.write(fd, data)
        os.fchmod(fd, mode)
        os.close(fd)
        os.replace(tmp_path, path)
    except BaseException:
        os.close(fd) if not _fd_closed(fd) else None
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _fd_closed(fd: int) -> bool:
    """Check if a file descriptor is already closed."""
    try:
        os.fstat(fd)
        return False
    except OSError:
        return True


def _parse_cert_expiry(cert_path: str) -> Optional[datetime.datetime]:
    """Parse the notAfter date from a PEM certificate file.

    Returns a timezone-aware datetime in UTC, or None if parsing fails.
    """
    try:
        from cryptography import x509

        with open(cert_path, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
        expiry = cert.not_valid_after_utc
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=datetime.timezone.utc)
        return expiry
    except Exception:
        return None
