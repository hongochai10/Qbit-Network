"""Lightweight IPFS HTTP API client using only stdlib urllib.

Connects to a local IPFS daemon (default: http://127.0.0.1:5001) and provides
methods for adding, retrieving, and pinning files.  No external dependencies.
"""

import io
import json
import os
import re
import urllib.request
import urllib.error
import uuid

# CID format: CIDv0 (Qm...) or CIDv1 (bafy...)
_CID_RE = re.compile(r'^(Qm[1-9A-HJ-NP-Za-km-z]{44}|b[a-z2-7]{58,})$')

# Default max file size: 10 MB
DEFAULT_MAX_FILE_SIZE = 10 * 1024 * 1024

# Timeouts
_ADD_TIMEOUT = 30   # seconds — uploading can be slow
_READ_TIMEOUT = 10  # seconds — cat / pin ls


class IPFSError(Exception):
    """Raised when an IPFS API call fails."""


class IPFSClient:
    """Minimal IPFS HTTP API client (no external deps)."""

    def __init__(self, api_url: str = "http://127.0.0.1:5001",
                 max_file_size: int = DEFAULT_MAX_FILE_SIZE):
        # Strip trailing slash
        self.api_url = api_url.rstrip("/")
        self.max_file_size = max_file_size

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return True if the IPFS daemon is reachable."""
        try:
            url = f"{self.api_url}/api/v0/id"
            req = urllib.request.Request(url, method="POST")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                return "ID" in data
        except Exception:
            return False

    def add_file(self, file_path: str) -> str:
        """Pin a file to IPFS and return its CID.

        Raises IPFSError on failure, ValueError if file too large.
        """
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        file_size = os.path.getsize(file_path)
        if file_size > self.max_file_size:
            raise ValueError(
                f"File too large: {file_size} bytes "
                f"(max {self.max_file_size} bytes / "
                f"{self.max_file_size // (1024 * 1024)} MB)")
        with open(file_path, "rb") as f:
            data = f.read()
        return self.add_bytes(data, os.path.basename(file_path))

    def add_bytes(self, data: bytes, filename: str = "data") -> str:
        """Pin raw bytes to IPFS and return the CID.

        Uses multipart/form-data encoding (what the IPFS HTTP API expects).
        """
        if len(data) > self.max_file_size:
            raise ValueError(
                f"Data too large: {len(data)} bytes "
                f"(max {self.max_file_size} bytes)")
        body, content_type = self._encode_multipart(data, filename)
        url = f"{self.api_url}/api/v0/add?pin=true"
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": content_type})
        try:
            with urllib.request.urlopen(req, timeout=_ADD_TIMEOUT) as resp:
                result = json.loads(resp.read())
                cid = result.get("Hash", "")
                if not cid:
                    raise IPFSError("IPFS add returned no CID")
                return cid
        except urllib.error.URLError as e:
            raise IPFSError(f"IPFS add failed: {e}") from e

    def cat(self, cid: str) -> bytes:
        """Retrieve file contents by CID."""
        self._validate_cid(cid)
        url = f"{self.api_url}/api/v0/cat?arg={cid}"
        req = urllib.request.Request(url, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=_READ_TIMEOUT) as resp:
                return resp.read()
        except urllib.error.URLError as e:
            raise IPFSError(f"IPFS cat failed for {cid}: {e}") from e

    def pin_ls(self, cid: str) -> bool:
        """Return True if the given CID is pinned locally."""
        self._validate_cid(cid)
        url = f"{self.api_url}/api/v0/pin/ls?arg={cid}&type=all"
        req = urllib.request.Request(url, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=_READ_TIMEOUT) as resp:
                result = json.loads(resp.read())
                keys = result.get("Keys", {})
                return cid in keys
        except urllib.error.HTTPError as e:
            # IPFS returns 500 if the pin is not found
            if e.code == 500:
                return False
            raise IPFSError(f"IPFS pin ls failed: {e}") from e
        except urllib.error.URLError as e:
            raise IPFSError(f"IPFS pin ls failed: {e}") from e

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def validate_cid(cid: str) -> bool:
        """Return True if the string looks like a valid CID."""
        return bool(_CID_RE.match(cid))

    @staticmethod
    def _validate_cid(cid: str):
        """Raise ValueError if CID format is invalid."""
        if not _CID_RE.match(cid):
            raise ValueError(
                f"Invalid CID format: {cid!r}. "
                f"Expected Qm... (CIDv0) or bafy... (CIDv1)")

    @staticmethod
    def _encode_multipart(data: bytes, filename: str) -> tuple[bytes, str]:
        """Build a multipart/form-data body for the IPFS /add endpoint."""
        boundary = uuid.uuid4().hex
        parts = []
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(
            f'Content-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\n'.encode())
        parts.append(b"Content-Type: application/octet-stream\r\n\r\n")
        parts.append(data)
        parts.append(f"\r\n--{boundary}--\r\n".encode())
        body = b"".join(parts)
        content_type = f"multipart/form-data; boundary={boundary}"
        return body, content_type
