"""Mutable byte buffer that can be securely zeroed.

Python ``bytes`` objects are immutable, so secret key material persists on the
heap even after the reference is dropped.  ``SecureBytes`` wraps a ctypes
array whose memory can be explicitly overwritten with zeros, preventing key
material from lingering in the process address space.
"""

import ctypes
import logging
import warnings

logger = logging.getLogger(__name__)

_CTYPES_AVAILABLE = True
try:
    ctypes.memset  # quick sanity check
except AttributeError:  # pragma: no cover
    _CTYPES_AVAILABLE = False


class SecureBytes:
    """A mutable byte buffer backed by ctypes that can be zeroed on demand.

    Unlike Python ``bytes``, the underlying memory can be overwritten with
    zeros to prevent secret key material from lingering in the process heap.

    Usage::

        sb = SecureBytes(raw_key_bytes)
        sig = MLDSA.sign(bytes(sb), msg)
        sb.zero()  # scrub key from memory

    Or as a context manager::

        with SecureBytes(raw_key_bytes) as sk:
            sig = MLDSA.sign(bytes(sk), msg)
        # key is zeroed on exit
    """

    __slots__ = ('_buf', '_len', '_zeroed')

    def __init__(self, data: bytes | bytearray):
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError(
                f"SecureBytes requires bytes-like input, got {type(data).__name__}")
        self._len = len(data)
        if _CTYPES_AVAILABLE:
            self._buf = (ctypes.c_char * self._len).from_buffer_copy(data)
        else:  # pragma: no cover
            warnings.warn(
                "ctypes unavailable — SecureBytes cannot guarantee zeroing",
                RuntimeWarning, stacklevel=2)
            self._buf = bytearray(data)
        self._zeroed = False

        # Best-effort zeroing of source if it is a mutable bytearray
        if isinstance(data, bytearray):
            for i in range(len(data)):
                data[i] = 0

    # ---- bytes-like interface ------------------------------------------------

    def __bytes__(self) -> bytes:
        if self._zeroed:
            raise ValueError("SecureBytes has been zeroed")
        return bytes(self._buf)

    def __len__(self) -> int:
        return self._len

    def hex(self) -> str:
        """Return hex representation (like bytes.hex)."""
        return bytes(self).hex()

    def __eq__(self, other: object) -> bool:
        if self._zeroed:
            return False
        if isinstance(other, SecureBytes):
            if other._zeroed:
                return False
            return bytes(self._buf) == bytes(other._buf)
        if isinstance(other, (bytes, bytearray)):
            return bytes(self._buf) == bytes(other)
        return NotImplemented

    def __hash__(self) -> int:
        if self._zeroed:
            raise ValueError("Cannot hash zeroed SecureBytes")
        return hash(bytes(self._buf))

    def __repr__(self) -> str:
        if self._zeroed:
            return f"SecureBytes(<zeroed, {self._len} bytes>)"
        return f"SecureBytes(<{self._len} bytes>)"

    def __bool__(self) -> bool:
        return self._len > 0 and not self._zeroed

    # ---- secure zeroing ------------------------------------------------------

    def zero(self) -> None:
        """Securely overwrite buffer contents with zeros."""
        if self._zeroed:
            return
        if _CTYPES_AVAILABLE and not isinstance(self._buf, bytearray):
            ctypes.memset(ctypes.addressof(self._buf), 0, self._len)
        else:  # pragma: no cover
            for i in range(self._len):
                self._buf[i] = 0
        self._zeroed = True

    @property
    def is_zeroed(self) -> bool:
        """True if the buffer has been zeroed."""
        return self._zeroed

    def __del__(self) -> None:
        try:
            self.zero()
        except Exception:
            pass

    # ---- context manager -----------------------------------------------------

    def __enter__(self) -> 'SecureBytes':
        return self

    def __exit__(self, *args: object) -> None:
        self.zero()


def wrap_secret(data: bytes | bytearray) -> 'SecureBytes':
    """Convenience factory: wrap raw bytes in SecureBytes.

    If ctypes is completely broken at import time (should not happen on
    CPython), this still returns a SecureBytes backed by a bytearray with
    best-effort zeroing.
    """
    return SecureBytes(data)
