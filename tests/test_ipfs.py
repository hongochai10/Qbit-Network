"""Tests for IPFS client and CLI IPFS integration.

These tests mock the IPFS daemon — no real IPFS node is required.
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli.ipfs_client import IPFSClient, IPFSError, DEFAULT_MAX_FILE_SIZE


# ========== IPFSClient unit tests ==========

class TestIPFSClientValidation(unittest.TestCase):
    """CID validation — no network required."""

    def test_valid_cidv0(self):
        # QmT... 46-char base58 (Qm + 44 chars)
        cid = "QmT5NvUtoM5nWFfrQdVrFtvGfKFmG7AHE8P34isapyhCxX"
        assert IPFSClient.validate_cid(cid) is True

    def test_valid_cidv1(self):
        cid = "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi"
        assert IPFSClient.validate_cid(cid) is True

    def test_invalid_cid_empty(self):
        assert IPFSClient.validate_cid("") is False

    def test_invalid_cid_random(self):
        assert IPFSClient.validate_cid("not-a-cid") is False

    def test_invalid_cid_local_ref(self):
        assert IPFSClient.validate_cid("local:abc123") is False

    def test_invalid_cid_short_qm(self):
        assert IPFSClient.validate_cid("QmShort") is False


class TestIPFSClientInit(unittest.TestCase):
    """Constructor and configuration."""

    def test_default_url(self):
        c = IPFSClient()
        assert c.api_url == "http://127.0.0.1:5001"

    def test_custom_url_strip_slash(self):
        c = IPFSClient(api_url="http://myhost:5001/")
        assert c.api_url == "http://myhost:5001"

    def test_custom_max_size(self):
        c = IPFSClient(max_file_size=1024)
        assert c.max_file_size == 1024

    def test_default_max_size(self):
        c = IPFSClient()
        assert c.max_file_size == DEFAULT_MAX_FILE_SIZE


class TestIPFSClientIsAvailable(unittest.TestCase):
    """is_available() checks daemon reachability."""

    @patch("urllib.request.urlopen")
    def test_available_true(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = json.dumps({"ID": "12D3..."}).encode()
        resp.__enter__ = lambda s: resp
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        c = IPFSClient()
        assert c.is_available() is True

    @patch("urllib.request.urlopen")
    def test_available_false_on_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        c = IPFSClient()
        assert c.is_available() is False

    @patch("urllib.request.urlopen")
    def test_available_false_no_id(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = json.dumps({"something": "else"}).encode()
        resp.__enter__ = lambda s: resp
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        c = IPFSClient()
        assert c.is_available() is False


class TestIPFSClientAddBytes(unittest.TestCase):
    """add_bytes() multipart upload."""

    @patch("urllib.request.urlopen")
    def test_add_bytes_success(self, mock_urlopen):
        fake_cid = "QmT5NvUtoM5nWFfrQdVrFtvGfKFmG7AHE8P34isapyhCxX"
        resp = MagicMock()
        resp.read.return_value = json.dumps({"Hash": fake_cid}).encode()
        resp.__enter__ = lambda s: resp
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        c = IPFSClient()
        result = c.add_bytes(b"hello world", "test.txt")
        assert result == fake_cid

    def test_add_bytes_too_large(self):
        c = IPFSClient(max_file_size=10)
        with self.assertRaises(ValueError) as ctx:
            c.add_bytes(b"x" * 100, "big.bin")
        assert "too large" in str(ctx.exception)

    @patch("urllib.request.urlopen")
    def test_add_bytes_no_hash(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = json.dumps({}).encode()
        resp.__enter__ = lambda s: resp
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        c = IPFSClient()
        with self.assertRaises(IPFSError):
            c.add_bytes(b"data", "test.txt")

    @patch("urllib.request.urlopen")
    def test_add_bytes_network_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("timeout")
        c = IPFSClient()
        with self.assertRaises(IPFSError):
            c.add_bytes(b"data", "test.txt")


class TestIPFSClientAddFile(unittest.TestCase):
    """add_file() reads file and delegates to add_bytes."""

    @patch("urllib.request.urlopen")
    def test_add_file_success(self, mock_urlopen):
        fake_cid = "QmT5NvUtoM5nWFfrQdVrFtvGfKFmG7AHE8P34isapyhCxX"
        resp = MagicMock()
        resp.read.return_value = json.dumps({"Hash": fake_cid}).encode()
        resp.__enter__ = lambda s: resp
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            f.write(b"test content")
            tmp = f.name
        try:
            c = IPFSClient()
            result = c.add_file(tmp)
            assert result == fake_cid
        finally:
            os.unlink(tmp)

    def test_add_file_not_found(self):
        c = IPFSClient()
        with self.assertRaises(FileNotFoundError):
            c.add_file("/nonexistent/path/file.txt")

    def test_add_file_too_large(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"x" * 200)
            tmp = f.name
        try:
            c = IPFSClient(max_file_size=100)
            with self.assertRaises(ValueError) as ctx:
                c.add_file(tmp)
            assert "too large" in str(ctx.exception)
        finally:
            os.unlink(tmp)


class TestIPFSClientCat(unittest.TestCase):
    """cat() retrieves file by CID."""

    @patch("urllib.request.urlopen")
    def test_cat_success(self, mock_urlopen):
        data = b"file contents here"
        resp = MagicMock()
        resp.read.return_value = data
        resp.__enter__ = lambda s: resp
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        c = IPFSClient()
        result = c.cat("QmT5NvUtoM5nWFfrQdVrFtvGfKFmG7AHE8P34isapyhCxX")
        assert result == data

    def test_cat_invalid_cid(self):
        c = IPFSClient()
        with self.assertRaises(ValueError):
            c.cat("invalid-cid")

    @patch("urllib.request.urlopen")
    def test_cat_network_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("timeout")
        c = IPFSClient()
        with self.assertRaises(IPFSError):
            c.cat("QmT5NvUtoM5nWFfrQdVrFtvGfKFmG7AHE8P34isapyhCxX")


class TestIPFSClientPinLs(unittest.TestCase):
    """pin_ls() checks if CID is pinned."""

    @patch("urllib.request.urlopen")
    def test_pin_ls_pinned(self, mock_urlopen):
        cid = "QmT5NvUtoM5nWFfrQdVrFtvGfKFmG7AHE8P34isapyhCxX"
        resp = MagicMock()
        resp.read.return_value = json.dumps(
            {"Keys": {cid: {"Type": "recursive"}}}).encode()
        resp.__enter__ = lambda s: resp
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        c = IPFSClient()
        assert c.pin_ls(cid) is True

    @patch("urllib.request.urlopen")
    def test_pin_ls_not_pinned_http500(self, mock_urlopen):
        cid = "QmT5NvUtoM5nWFfrQdVrFtvGfKFmG7AHE8P34isapyhCxX"
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="", code=500, msg="", hdrs=None, fp=None)
        c = IPFSClient()
        assert c.pin_ls(cid) is False

    def test_pin_ls_invalid_cid(self):
        c = IPFSClient()
        with self.assertRaises(ValueError):
            c.pin_ls("bad-cid")


class TestMultipartEncoding(unittest.TestCase):
    """Multipart form encoding for IPFS /add."""

    def test_encode_contains_data(self):
        data = b"hello IPFS"
        body, ct = IPFSClient._encode_multipart(data, "test.txt")
        assert b"hello IPFS" in body
        assert "multipart/form-data" in ct
        assert "boundary=" in ct

    def test_encode_contains_filename(self):
        body, _ = IPFSClient._encode_multipart(b"x", "my_doc.pdf")
        assert b"my_doc.pdf" in body


# ========== CLI integration tests (mocked RPC + IPFS) ==========

class TestCLIStoreIPFS(unittest.TestCase):
    """cmd_store with --ipfs flag."""

    @patch("cli.qbit._rpc_call")
    @patch("cli.qbit._get_ipfs_client")
    def test_store_ipfs_pin_success(self, mock_get_client, mock_rpc):
        """--ipfs flag pins to IPFS and uses CID in tx."""
        fake_cid = "QmT5NvUtoM5nWFfrQdVrFtvGfKFmG7AHE8P34isapyhCxX"
        mock_client = MagicMock()
        mock_client.add_file.return_value = fake_cid
        mock_get_client.return_value = mock_client

        mock_rpc.return_value = {"result": {"tx_id": "abc123"}}

        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            f.write(b"test doc")
            tmp = f.name
        try:
            from cli.qbit import cmd_store
            args = MagicMock()
            args.file = tmp
            args.wallet = "qv1" + "a" * 64
            args.token = "test-token"
            args.cid = ""
            args.metadata = ""
            args.ipfs = True
            args.ipfs_api = "http://127.0.0.1:5001"
            args.max_file_size = 10 * 1024 * 1024
            args.json = True
            args.rpc = "http://localhost:8545"
            args.insecure = False

            import io
            captured = io.StringIO()
            with patch("sys.stdout", captured):
                cmd_store(args)

            output = json.loads(captured.getvalue())
            assert output["cid"] == fake_cid
            assert output["ipfs_pinned"] is True
        finally:
            os.unlink(tmp)

    @patch("cli.qbit._rpc_call")
    @patch("cli.qbit._get_ipfs_client")
    def test_store_ipfs_unavailable_fallback(self, mock_get_client, mock_rpc):
        """When IPFS unavailable, falls back to local ref."""
        mock_get_client.return_value = None
        mock_rpc.return_value = {"result": {"tx_id": "abc123"}}

        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            f.write(b"test doc")
            tmp = f.name
        try:
            from cli.qbit import cmd_store
            args = MagicMock()
            args.file = tmp
            args.wallet = "qv1" + "a" * 64
            args.token = "test-token"
            args.cid = ""
            args.metadata = ""
            args.ipfs = True
            args.ipfs_api = "http://127.0.0.1:5001"
            args.max_file_size = 10 * 1024 * 1024
            args.json = True
            args.rpc = "http://localhost:8545"
            args.insecure = False

            import io
            captured = io.StringIO()
            with patch("sys.stdout", captured), \
                 patch("sys.stderr", io.StringIO()):
                cmd_store(args)

            output = json.loads(captured.getvalue())
            assert output["cid"].startswith("local:")
            assert output["ipfs_pinned"] is False
        finally:
            os.unlink(tmp)

    @patch("cli.qbit._rpc_call")
    def test_store_no_ipfs_flag(self, mock_rpc):
        """Without --ipfs, uses local ref as before."""
        mock_rpc.return_value = {"result": {"tx_id": "abc123"}}

        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            f.write(b"test doc")
            tmp = f.name
        try:
            from cli.qbit import cmd_store
            args = MagicMock()
            args.file = tmp
            args.wallet = "qv1" + "a" * 64
            args.token = "test-token"
            args.cid = ""
            args.metadata = ""
            args.ipfs = False
            args.json = True
            args.rpc = "http://localhost:8545"
            args.insecure = False

            import io
            captured = io.StringIO()
            with patch("sys.stdout", captured):
                cmd_store(args)

            output = json.loads(captured.getvalue())
            assert output["cid"].startswith("local:")
            assert output["ipfs_pinned"] is False
        finally:
            os.unlink(tmp)

    @patch("cli.qbit._rpc_call")
    def test_store_manual_cid(self, mock_rpc):
        """Manual --cid overrides IPFS pinning."""
        mock_rpc.return_value = {"result": {"tx_id": "abc123"}}
        manual_cid = "QmT5NvUtoM5nWFfrQdVrFtvGfKFmG7AHE8P34isapyhCxX"

        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            f.write(b"test doc")
            tmp = f.name
        try:
            from cli.qbit import cmd_store
            args = MagicMock()
            args.file = tmp
            args.wallet = "qv1" + "a" * 64
            args.token = "test-token"
            args.cid = manual_cid
            args.metadata = ""
            args.ipfs = True  # even with --ipfs, manual CID takes precedence
            args.json = True
            args.rpc = "http://localhost:8545"
            args.insecure = False

            import io
            captured = io.StringIO()
            with patch("sys.stdout", captured):
                cmd_store(args)

            output = json.loads(captured.getvalue())
            assert output["cid"] == manual_cid
        finally:
            os.unlink(tmp)


class TestCLIShareIPFS(unittest.TestCase):
    """cmd_share with --ipfs flag."""

    @patch("cli.qbit._rpc_call")
    @patch("cli.qbit._get_ipfs_client")
    def test_share_ipfs_pin_success(self, mock_get_client, mock_rpc):
        fake_cid = "QmT5NvUtoM5nWFfrQdVrFtvGfKFmG7AHE8P34isapyhCxX"
        mock_client = MagicMock()
        mock_client.add_bytes.return_value = fake_cid
        mock_client.max_file_size = 10 * 1024 * 1024
        mock_get_client.return_value = mock_client

        mock_rpc.return_value = {"result": {"tx_id": "share123"}}

        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            f.write(b"secret doc")
            tmp = f.name
        try:
            from cli.qbit import cmd_share
            args = MagicMock()
            args.file = tmp
            args.wallet = "qv1" + "a" * 64
            args.to = "qv1" + "b" * 64
            args.token = "test-token"
            args.cid = ""
            args.expires = 0
            args.ipfs = True
            args.ipfs_api = "http://127.0.0.1:5001"
            args.max_file_size = 10 * 1024 * 1024
            args.json = True
            args.rpc = "http://localhost:8545"
            args.insecure = False

            import io
            captured = io.StringIO()
            with patch("sys.stdout", captured):
                cmd_share(args)

            output = json.loads(captured.getvalue())
            assert output["cid"] == fake_cid
            assert output["ipfs_pinned"] is True
        finally:
            os.unlink(tmp)


class TestCLIRetrieve(unittest.TestCase):
    """cmd_retrieve fetches from IPFS."""

    def test_retrieve_invalid_cid(self):
        """Invalid CID format should fail."""
        from cli.qbit import cmd_retrieve
        import argparse
        args = argparse.Namespace(
            cid="not-a-valid-cid",
            json=False,
            rpc="http://localhost:8545",
            insecure=False,
            output="",
            verify_hash=False,
            ipfs_api="http://127.0.0.1:5001",
        )

        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.stderr", io.StringIO()):
                cmd_retrieve(args)
        assert ctx.exception.code == 1

    @patch("urllib.request.urlopen")
    def test_retrieve_to_file(self, mock_urlopen):
        """Retrieve CID and save to output file."""
        cid = "QmT5NvUtoM5nWFfrQdVrFtvGfKFmG7AHE8P34isapyhCxX"
        file_data = b"retrieved content"

        # Mock is_available
        id_resp = MagicMock()
        id_resp.read.return_value = json.dumps({"ID": "12D3..."}).encode()
        id_resp.__enter__ = lambda s: id_resp
        id_resp.__exit__ = MagicMock(return_value=False)

        # Mock cat
        cat_resp = MagicMock()
        cat_resp.read.return_value = file_data
        cat_resp.__enter__ = lambda s: cat_resp
        cat_resp.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [id_resp, cat_resp]

        with tempfile.NamedTemporaryFile(delete=False, suffix=".out") as f:
            out_path = f.name

        try:
            from cli.qbit import cmd_retrieve
            args = MagicMock()
            args.cid = cid
            args.output = out_path
            args.verify_hash = False
            args.ipfs_api = "http://127.0.0.1:5001"
            args.json = True
            args.rpc = "http://localhost:8545"
            args.insecure = False

            captured = io.StringIO()
            with patch("sys.stdout", captured):
                cmd_retrieve(args)

            output = json.loads(captured.getvalue())
            assert output["cid"] == cid
            assert output["size"] == len(file_data)

            with open(out_path, "rb") as f:
                assert f.read() == file_data
        finally:
            os.unlink(out_path)


import io


if __name__ == "__main__":
    unittest.main()
