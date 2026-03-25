"""Tests for cli/qbit.py — CLI command parsing, helpers, and edge cases."""
import argparse
import io
import json
import os
import sys
import tempfile
import shutil
import pytest
from unittest.mock import patch, MagicMock

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import importlib


# =========================================================================
# Helpers
# =========================================================================

class TestFileHash:
    """_file_hash computes SHA3-256 of file contents."""

    def test_deterministic(self, tmp_path):
        from cli.qbit import _file_hash
        f = tmp_path / "test.txt"
        f.write_bytes(b"hello world")
        h1 = _file_hash(str(f))
        h2 = _file_hash(str(f))
        assert h1 == h2

    def test_known_value(self, tmp_path):
        import hashlib
        from cli.qbit import _file_hash
        content = b"qbit blockchain"
        expected = hashlib.sha3_256(content).hexdigest()
        f = tmp_path / "doc.txt"
        f.write_bytes(content)
        assert _file_hash(str(f)) == expected

    def test_empty_file(self, tmp_path):
        import hashlib
        from cli.qbit import _file_hash
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        expected = hashlib.sha3_256(b"").hexdigest()
        assert _file_hash(str(f)) == expected

    def test_large_file_chunks(self, tmp_path):
        from cli.qbit import _file_hash
        # 20KB file to exercise chunked reading
        content = b"x" * 20000
        f = tmp_path / "large.bin"
        f.write_bytes(content)
        h = _file_hash(str(f))
        assert len(h) == 64


class TestWalletsDir:
    """_wallets_dir creates the directory if it doesn't exist."""

    def test_creates_directory(self, tmp_path):
        with patch("qbit_network.config.DATA_DIR", str(tmp_path)):
            from cli import qbit as cli_mod
            # Re-import to pick up patched config
            importlib.reload(cli_mod)
            d = cli_mod._wallets_dir()
            assert os.path.isdir(d)

    def test_returns_path_under_data_dir(self, tmp_path):
        with patch("qbit_network.config.DATA_DIR", str(tmp_path)):
            from cli import qbit as cli_mod
            importlib.reload(cli_mod)
            d = cli_mod._wallets_dir()
            assert d.startswith(str(tmp_path))


class TestRpcCall:
    """_rpc_call formats and sends a JSON-RPC request."""

    @patch("urllib.request.urlopen")
    def test_basic_call(self, mock_urlopen):
        from cli.qbit import _rpc_call
        resp = MagicMock()
        resp.read.return_value = json.dumps({"jsonrpc": "2.0", "id": 1, "result": "ok"}).encode()
        resp.__enter__ = lambda s: resp
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp
        result = _rpc_call("http://localhost:8545", "qv_blockNumber")
        assert result["result"] == "ok"

    @patch("urllib.request.urlopen")
    def test_auth_token_sent(self, mock_urlopen):
        from cli.qbit import _rpc_call
        import urllib.request
        resp = MagicMock()
        resp.read.return_value = json.dumps({"result": True}).encode()
        resp.__enter__ = lambda s: resp
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp
        _rpc_call("http://localhost:8545", "qv_notarize", token="mytoken")
        call_args = mock_urlopen.call_args
        # First arg is the Request object
        req = call_args[0][0]
        assert req.get_header("Authorization") == "Bearer mytoken"

    @patch("urllib.request.urlopen")
    def test_insecure_disables_tls_verify(self, mock_urlopen, capsys):
        from cli.qbit import _rpc_call
        resp = MagicMock()
        resp.read.return_value = json.dumps({"result": True}).encode()
        resp.__enter__ = lambda s: resp
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp
        _rpc_call("https://localhost:8545", "qv_blockNumber", verify_ssl=False)
        # Should have passed a context arg to urlopen
        call_kwargs = mock_urlopen.call_args[1]
        assert "context" in call_kwargs
        assert call_kwargs["context"] is not None


# =========================================================================
# cmd_wallet_create
# =========================================================================

class TestCmdWalletCreate:

    def test_creates_wallet_file(self, tmp_path):
        import cli.qbit as cli_mod
        wallets_dir = str(tmp_path / "wallets")
        os.makedirs(wallets_dir)
        with patch.object(cli_mod, "_wallets_dir", return_value=wallets_dir):
            args = MagicMock()
            args.no_password = True
            args.register = False
            args.json = True
            args.rpc = "http://localhost:8545"
            args.insecure = False

            captured = io.StringIO()
            with patch("sys.stdout", captured):
                cli_mod.cmd_wallet_create(args)

            output = json.loads(captured.getvalue())
            assert "address" in output
            assert output["address"].startswith("qv1")
            assert os.path.isfile(output["path"])

    def test_json_output_contains_registered_false(self, tmp_path):
        import cli.qbit as cli_mod
        wallets_dir = str(tmp_path / "wallets")
        os.makedirs(wallets_dir)
        with patch.object(cli_mod, "_wallets_dir", return_value=wallets_dir):
            args = MagicMock()
            args.no_password = True
            args.register = False
            args.json = True
            args.rpc = "http://localhost:8545"
            args.insecure = False

            captured = io.StringIO()
            with patch("sys.stdout", captured):
                cli_mod.cmd_wallet_create(args)

            output = json.loads(captured.getvalue())
            assert output["registered"] is False

    def test_text_output_shows_address(self, tmp_path):
        import cli.qbit as cli_mod
        wallets_dir = str(tmp_path / "wallets")
        os.makedirs(wallets_dir)
        with patch.object(cli_mod, "_wallets_dir", return_value=wallets_dir):
            args = MagicMock()
            args.no_password = True
            args.register = False
            args.json = False
            args.rpc = "http://localhost:8545"
            args.insecure = False

            captured = io.StringIO()
            with patch("sys.stdout", captured):
                cli_mod.cmd_wallet_create(args)

            output = captured.getvalue()
            assert "qv1" in output


# =========================================================================
# cmd_wallet_list
# =========================================================================

class TestCmdWalletList:

    def test_lists_created_wallets(self, tmp_path):
        """Test wallet listing by using _load_all_wallets directly with a specific dir."""
        from qbit_network.core.wallet import Wallet
        from cli.qbit import _ADDR_RE
        import cli.qbit as cli_mod

        wallets_dir = str(tmp_path / "wallets")
        os.makedirs(wallets_dir)

        # Create two wallets directly
        created = []
        for _ in range(2):
            w = Wallet.generate()
            path = os.path.join(wallets_dir, f"{w.address}.json")
            w.save(path, password="")
            created.append(w.address)

        # Patch _wallets_dir to return our dir
        with patch.object(cli_mod, "_wallets_dir", return_value=wallets_dir):
            wallets = cli_mod._load_all_wallets()
        assert len(wallets) == 2
        for addr in created:
            assert addr in wallets

    def test_empty_list_shows_message(self, tmp_path):
        import cli.qbit as cli_mod
        wallets_dir = str(tmp_path / "wallets")
        os.makedirs(wallets_dir)
        with patch.object(cli_mod, "_wallets_dir", return_value=wallets_dir):
            args = MagicMock()
            args.json = False
            captured = io.StringIO()
            with patch("sys.stdout", captured):
                cli_mod.cmd_wallet_list(args)
            assert "No wallets" in captured.getvalue()

    def test_json_output_is_valid_list(self, tmp_path):
        import cli.qbit as cli_mod
        wallets_dir = str(tmp_path / "wallets")
        os.makedirs(wallets_dir)
        with patch.object(cli_mod, "_wallets_dir", return_value=wallets_dir):
            args = MagicMock()
            args.json = True
            captured = io.StringIO()
            with patch("sys.stdout", captured):
                cli_mod.cmd_wallet_list(args)
            output = json.loads(captured.getvalue())
            assert isinstance(output, list)


# =========================================================================
# cmd_verify_proof (offline proof verification)
# =========================================================================

class TestCmdVerifyProof:

    def _make_proof_file(self, tmp_path, valid=True):
        """Build a valid or tampered proof file for testing."""
        from qbit_network.core.wallet import Wallet
        from qbit_network.core.transaction import Transaction
        from qbit_network.core.blockchain import Blockchain
        from qbit_network.core.proof import export_proof

        w = Wallet.generate()
        bc = Blockchain()
        bc.consensus.add_validator(w.address, w.signing_pk)
        bc.init_chain(w.address, w.signing_sk)

        doc = "aabb" * 8
        tx = Transaction.notarize(w.address, doc, nonce=0)
        tx.sign(w.signing_sk, w.signing_pk)
        bc.submit_tx(tx)
        block = bc.produce_block(w.address, w.signing_sk)

        proof = export_proof(block.to_dict(), 0, tx.tx_id, doc, w.address)
        if not valid:
            proof["tx_id"] = "ff" * 32

        proof_file = str(tmp_path / "proof.json")
        with open(proof_file, "w") as f:
            json.dump(proof, f)
        return proof_file

    def test_valid_proof_exits_0(self, tmp_path):
        from cli.qbit import cmd_verify_proof
        proof_file = self._make_proof_file(tmp_path)
        args = MagicMock()
        args.proof_file = proof_file
        args.json = False
        # Should not raise SystemExit
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            cmd_verify_proof(args)
        assert "PROOF VALID" in captured.getvalue()

    def test_invalid_proof_exits_1(self, tmp_path):
        from cli.qbit import cmd_verify_proof
        proof_file = self._make_proof_file(tmp_path, valid=False)
        args = MagicMock()
        args.proof_file = proof_file
        args.json = False
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.stdout", io.StringIO()):
                cmd_verify_proof(args)
        assert exc_info.value.code == 1

    def test_valid_proof_json_output(self, tmp_path):
        from cli.qbit import cmd_verify_proof
        proof_file = self._make_proof_file(tmp_path)
        args = MagicMock()
        args.proof_file = proof_file
        args.json = True
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            cmd_verify_proof(args)
        output = json.loads(captured.getvalue())
        assert output["valid"] is True
        assert output["errors"] == []

    def test_invalid_proof_json_output(self, tmp_path):
        from cli.qbit import cmd_verify_proof
        proof_file = self._make_proof_file(tmp_path, valid=False)
        args = MagicMock()
        args.proof_file = proof_file
        args.json = True
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            cmd_verify_proof(args)
        output = json.loads(captured.getvalue())
        assert output["valid"] is False
        assert len(output["errors"]) > 0

    def test_wrong_type_field_exits_1(self, tmp_path):
        from cli.qbit import cmd_verify_proof
        proof_data = {
            "type": "wrong-type",
            "version": 1,
            "tx_id": "aa" * 32,
            "block": {},
            "merkle_proof": [],
            "document_hash": "bb" * 32,
        }
        proof_file = str(tmp_path / "bad_proof.json")
        with open(proof_file, "w") as f:
            json.dump(proof_data, f)
        args = MagicMock()
        args.proof_file = proof_file
        args.json = False
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.stderr", io.StringIO()):
                cmd_verify_proof(args)
        assert exc_info.value.code == 1


# =========================================================================
# cmd_notarize — missing file exits with code 1
# =========================================================================

class TestCmdNotarize:

    def test_missing_file_exits_1(self, tmp_path):
        from cli.qbit import cmd_notarize
        args = MagicMock()
        args.file = str(tmp_path / "nonexistent.txt")
        args.wallet = "qv1" + "a" * 64
        args.token = "tok"
        args.metadata = ""
        args.json = False
        args.rpc = "http://localhost:8545"
        args.insecure = False
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.stderr", io.StringIO()):
                cmd_notarize(args)
        assert exc_info.value.code == 1

    @patch("cli.qbit._rpc_call")
    def test_success_json_output(self, mock_rpc, tmp_path):
        from cli.qbit import cmd_notarize
        with patch("qbit_network.config.DATA_DIR", str(tmp_path)):
            mock_rpc.return_value = {"result": {"tx_id": "txabc123"}}
            f = tmp_path / "doc.txt"
            f.write_bytes(b"hello document")
            args = MagicMock()
            args.file = str(f)
            args.wallet = "qv1" + "a" * 64
            args.token = "tok"
            args.metadata = "my doc"
            args.json = True
            args.rpc = "http://localhost:8545"
            args.insecure = False

            captured = io.StringIO()
            with patch("sys.stdout", captured):
                cmd_notarize(args)
            output = json.loads(captured.getvalue())
            assert output["tx_id"] == "txabc123"
            assert "document_hash" in output

    @patch("cli.qbit._rpc_call")
    def test_rpc_error_exits_1(self, mock_rpc, tmp_path):
        from cli.qbit import cmd_notarize
        mock_rpc.return_value = {"error": {"message": "bad request"}}
        f = tmp_path / "doc.txt"
        f.write_bytes(b"content")
        args = MagicMock()
        args.file = str(f)
        args.wallet = "qv1" + "a" * 64
        args.token = "tok"
        args.metadata = ""
        args.json = False
        args.rpc = "http://localhost:8545"
        args.insecure = False
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.stderr", io.StringIO()):
                cmd_notarize(args)
        assert exc_info.value.code == 1

    @patch("cli.qbit._rpc_call")
    def test_auto_selects_single_wallet(self, mock_rpc, tmp_path):
        """When no --wallet is given and only one wallet exists, it's auto-selected."""
        import cli.qbit as cli_mod
        from qbit_network.core.wallet import Wallet

        wallets_dir = str(tmp_path / "wallets")
        os.makedirs(wallets_dir)

        # Create exactly one wallet
        w = Wallet.generate()
        path = os.path.join(wallets_dir, f"{w.address}.json")
        w.save(path, password="")

        mock_rpc.return_value = {"result": {"tx_id": "txauto"}}
        f = tmp_path / "doc.txt"
        f.write_bytes(b"auto wallet test")

        args = MagicMock()
        args.file = str(f)
        args.wallet = ""  # no wallet specified
        args.token = "tok"
        args.metadata = ""
        args.json = True
        args.rpc = "http://localhost:8545"
        args.insecure = False

        with patch.object(cli_mod, "_wallets_dir", return_value=wallets_dir):
            captured = io.StringIO()
            with patch("sys.stdout", captured):
                cli_mod.cmd_notarize(args)
        output = json.loads(captured.getvalue())
        assert output["tx_id"] == "txauto"


# =========================================================================
# cmd_verify
# =========================================================================

class TestCmdVerify:

    def test_missing_file_exits_1(self, tmp_path):
        from cli.qbit import cmd_verify
        args = MagicMock()
        args.file = str(tmp_path / "nonexistent.txt")
        args.json = False
        args.rpc = "http://localhost:8545"
        args.insecure = False
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.stderr", io.StringIO()):
                cmd_verify(args)
        assert exc_info.value.code == 1

    @patch("cli.qbit._rpc_call")
    def test_not_notarized_exits_1(self, mock_rpc, tmp_path):
        from cli.qbit import cmd_verify
        mock_rpc.return_value = {"result": None}
        f = tmp_path / "doc.txt"
        f.write_bytes(b"not notarized")
        args = MagicMock()
        args.file = str(f)
        args.json = False
        args.rpc = "http://localhost:8545"
        args.insecure = False
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.stdout", io.StringIO()):
                cmd_verify(args)
        assert exc_info.value.code == 1

    @patch("cli.qbit._rpc_call")
    def test_verified_json_output(self, mock_rpc, tmp_path):
        from cli.qbit import cmd_verify
        import time
        proof_data = {
            "tx_id": "txverify123",
            "block_index": 5,
            "timestamp": int(time.time()),
            "sender": "qv1" + "a" * 64,
        }
        mock_rpc.return_value = {"result": proof_data}
        f = tmp_path / "doc.txt"
        f.write_bytes(b"verified content")
        args = MagicMock()
        args.file = str(f)
        args.json = True
        args.rpc = "http://localhost:8545"
        args.insecure = False
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            cmd_verify(args)
        output = json.loads(captured.getvalue())
        assert output["verified"] is True
        assert "document_hash" in output


# =========================================================================
# cmd_store
# =========================================================================

class TestCmdStore:

    def test_missing_file_exits_1(self, tmp_path):
        from cli.qbit import cmd_store
        args = MagicMock()
        args.file = str(tmp_path / "nonexistent.txt")
        args.wallet = "qv1" + "a" * 64
        args.token = "tok"
        args.json = False
        args.rpc = "http://localhost:8545"
        args.insecure = False
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.stderr", io.StringIO()):
                cmd_store(args)
        assert exc_info.value.code == 1

    @patch("cli.qbit._rpc_call")
    def test_success_with_manual_cid(self, mock_rpc, tmp_path):
        from cli.qbit import cmd_store
        mock_rpc.return_value = {"result": {"tx_id": "txstore123"}}
        f = tmp_path / "doc.txt"
        f.write_bytes(b"store content")
        args = MagicMock()
        args.file = str(f)
        args.wallet = "qv1" + "a" * 64
        args.token = "tok"
        args.cid = "QmT5NvUtoM5nWFfrQdVrFtvGfKFmG7AHE8P34isapyhCxX"
        args.metadata = ""
        args.ipfs = False
        args.json = True
        args.rpc = "http://localhost:8545"
        args.insecure = False

        captured = io.StringIO()
        with patch("sys.stdout", captured):
            cmd_store(args)
        output = json.loads(captured.getvalue())
        assert output["tx_id"] == "txstore123"
        assert output["cid"] == args.cid

    @patch("cli.qbit._rpc_call")
    def test_no_wallet_multiple_exits_1(self, mock_rpc, tmp_path):
        """Multiple wallets found without --wallet specified exits with code 1."""
        import cli.qbit as cli_mod
        from qbit_network.core.wallet import Wallet

        wallets_dir = str(tmp_path / "wallets")
        os.makedirs(wallets_dir)
        # Create two wallets
        for _ in range(2):
            w = Wallet.generate()
            path = os.path.join(wallets_dir, f"{w.address}.json")
            w.save(path, password="")

        f = tmp_path / "doc.txt"
        f.write_bytes(b"content")
        args = MagicMock()
        args.file = str(f)
        args.wallet = ""  # not specified
        args.token = "tok"
        args.cid = ""
        args.metadata = ""
        args.ipfs = False
        args.json = False
        args.rpc = "http://localhost:8545"
        args.insecure = False

        with patch.object(cli_mod, "_wallets_dir", return_value=wallets_dir):
            with pytest.raises(SystemExit) as exc_info:
                with patch("sys.stderr", io.StringIO()):
                    cli_mod.cmd_store(args)
        assert exc_info.value.code == 1


# =========================================================================
# cmd_share
# =========================================================================

class TestCmdShare:

    def test_missing_file_exits_1(self, tmp_path):
        from cli.qbit import cmd_share
        args = MagicMock()
        args.file = str(tmp_path / "nonexistent.txt")
        args.wallet = "qv1" + "a" * 64
        args.to = "qv1" + "b" * 64
        args.token = "tok"
        args.json = False
        args.rpc = "http://localhost:8545"
        args.insecure = False
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.stderr", io.StringIO()):
                cmd_share(args)
        assert exc_info.value.code == 1

    def test_no_recipient_exits_1(self, tmp_path):
        from cli.qbit import cmd_share
        f = tmp_path / "doc.txt"
        f.write_bytes(b"content")
        args = MagicMock()
        args.file = str(f)
        args.wallet = "qv1" + "a" * 64
        args.to = ""  # no recipient
        args.token = "tok"
        args.json = False
        args.rpc = "http://localhost:8545"
        args.insecure = False
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.stderr", io.StringIO()):
                cmd_share(args)
        assert exc_info.value.code == 1

    @patch("cli.qbit._rpc_call")
    def test_success_json_output(self, mock_rpc, tmp_path):
        from cli.qbit import cmd_share
        mock_rpc.return_value = {"result": {"tx_id": "txshare123"}}
        f = tmp_path / "doc.txt"
        f.write_bytes(b"share content")
        args = MagicMock()
        args.file = str(f)
        args.wallet = "qv1" + "a" * 64
        args.to = "qv1" + "b" * 64
        args.token = "tok"
        args.cid = "QmT5NvUtoM5nWFfrQdVrFtvGfKFmG7AHE8P34isapyhCxX"
        args.expires = 0
        args.ipfs = False
        args.json = True
        args.rpc = "http://localhost:8545"
        args.insecure = False

        captured = io.StringIO()
        with patch("sys.stdout", captured):
            cmd_share(args)
        output = json.loads(captured.getvalue())
        assert output["tx_id"] == "txshare123"


# =========================================================================
# cmd_retrieve edge cases
# =========================================================================

class TestCmdRetrieve:

    def test_invalid_cid_exits_1(self, tmp_path):
        from cli.qbit import cmd_retrieve
        args = argparse.Namespace(
            cid="not-a-valid-cid",
            json=False,
            rpc="http://localhost:8545",
            insecure=False,
            output="",
            verify_hash=False,
            ipfs_api="http://127.0.0.1:5001",
        )
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.stderr", io.StringIO()):
                cmd_retrieve(args)
        assert exc_info.value.code == 1

    def test_cidv0_valid_format(self):
        """Valid CIDv0 format doesn't exit before trying IPFS."""
        from cli.ipfs_client import IPFSClient
        # Should be True for a proper CIDv0
        assert IPFSClient.validate_cid("QmT5NvUtoM5nWFfrQdVrFtvGfKFmG7AHE8P34isapyhCxX") is True

    def test_cidv1_valid_format(self):
        from cli.ipfs_client import IPFSClient
        assert IPFSClient.validate_cid(
            "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi") is True


# =========================================================================
# Argument parser
# =========================================================================

class TestArgumentParser:
    """Test that the argument parser accepts expected commands."""

    def test_wallet_create_parsed(self):
        from cli.qbit import main
        # main() with no args should print help (may exit 0 or no-op)
        with patch("sys.argv", ["qbit"]):
            captured = io.StringIO()
            with patch("sys.stdout", captured):
                try:
                    main()
                except SystemExit:
                    pass

    def test_notarize_command_recognized(self, tmp_path):
        """notarize command is routed to cmd_notarize."""
        import cli.qbit as cli_mod
        f = tmp_path / "doc.txt"
        f.write_bytes(b"content")
        called = []
        def fake_cmd_notarize(args):
            called.append(True)
        with patch("sys.argv", ["qbit", "notarize", str(f)]), \
             patch.object(cli_mod, "cmd_notarize", side_effect=fake_cmd_notarize):
            cli_mod.main()
        assert called

    def test_verify_proof_command_recognized(self, tmp_path):
        """verify-proof command is routed to cmd_verify_proof."""
        import cli.qbit as cli_mod
        proof_file = tmp_path / "p.json"
        with open(str(proof_file), "w") as f:
            json.dump({"type": "other"}, f)
        called = []
        def fake_cmd(args):
            called.append(True)
        with patch("sys.argv", ["qbit", "verify-proof", str(proof_file)]), \
             patch.object(cli_mod, "cmd_verify_proof", side_effect=fake_cmd):
            cli_mod.main()
        assert called

    def test_store_command_flags(self, tmp_path):
        """store command accepts --ipfs and --max-file-size flags."""
        import cli.qbit as cli_mod
        f = tmp_path / "doc.txt"
        f.write_bytes(b"content")
        called = []
        def fake_cmd(args):
            called.append(args)
        with patch("sys.argv", [
            "qbit", "store", str(f),
            "--wallet", "qv1" + "a" * 64,
            "--token", "tok",
            "--ipfs",
            "--max-file-size", "5242880",
        ]), patch.object(cli_mod, "cmd_store", side_effect=fake_cmd):
            cli_mod.main()
        assert called
        assert called[0].ipfs is True
        assert called[0].max_file_size == 5242880

    def test_share_command_has_expires_flag(self, tmp_path):
        """share command accepts --expires flag."""
        import cli.qbit as cli_mod
        f = tmp_path / "doc.txt"
        f.write_bytes(b"content")
        called = []
        def fake_cmd(args):
            called.append(args)
        with patch("sys.argv", [
            "qbit", "share", str(f),
            "--to", "qv1" + "b" * 64,
            "--wallet", "qv1" + "a" * 64,
            "--token", "tok",
            "--expires", "9999999999",
            "--cid", "QmT5NvUtoM5nWFfrQdVrFtvGfKFmG7AHE8P34isapyhCxX",
        ]), patch.object(cli_mod, "cmd_share", side_effect=fake_cmd):
            cli_mod.main()
        assert called
        assert called[0].expires == 9999999999


# =========================================================================
# _load_all_wallets — address validation
# =========================================================================

class TestLoadAllWallets:

    def test_filters_invalid_address_format(self, tmp_path):
        import cli.qbit as cli_mod

        wallets_dir = os.path.join(str(tmp_path), "wallets")
        os.makedirs(wallets_dir, exist_ok=True)
        # Write a file with invalid address
        bad_wallet_path = os.path.join(wallets_dir, "badwallet.json")
        with open(bad_wallet_path, "w") as f:
            json.dump({"address": "not-a-valid-address"}, f)

        # Should not include the invalid wallet
        with patch.object(cli_mod, "_wallets_dir", return_value=wallets_dir):
            wallets = cli_mod._load_all_wallets()
        assert "not-a-valid-address" not in wallets

    def test_skips_non_json_files(self, tmp_path):
        import cli.qbit as cli_mod

        wallets_dir = os.path.join(str(tmp_path), "wallets")
        os.makedirs(wallets_dir, exist_ok=True)
        # Write a non-json file
        with open(os.path.join(wallets_dir, "notes.txt"), "w") as f:
            f.write("this is not a wallet")

        with patch.object(cli_mod, "_wallets_dir", return_value=wallets_dir):
            wallets = cli_mod._load_all_wallets()
        assert len(wallets) == 0
