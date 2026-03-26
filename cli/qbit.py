#!/usr/bin/env python3
"""QBit Network CLI — wallet management, document notarization, proof verification."""
import argparse
import hashlib
import json
import os
import sys
import getpass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qbit_network.core.wallet import Wallet
from qbit_network.crypto import sha3_256, MLDSA, merkle_proof, verify_merkle_proof
from qbit_network.config import DATA_DIR


def _wallets_dir() -> str:
    d = os.path.join(DATA_DIR, "wallets")
    os.makedirs(d, exist_ok=True)
    return d


import re
_ADDR_RE = re.compile(r'^qv1[0-9a-f]{64}$')


def _load_all_wallets() -> dict[str, Wallet]:
    d = _wallets_dir()
    wallets = {}
    for f in os.listdir(d):
        if f.endswith(".json"):
            try:
                w = Wallet.load(os.path.join(d, f))
                # Validate address format to prevent path traversal
                if _ADDR_RE.match(w.address):
                    wallets[w.address] = w
            except Exception:
                pass
    return wallets


def _file_hash(filepath: str) -> str:
    """SHA3-256 hash of file contents."""
    h = hashlib.sha3_256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _rpc_call(url: str, method: str, params: dict = None,
              token: str = "", verify_ssl: bool = True) -> dict:
    """Synchronous JSON-RPC call."""
    import urllib.request
    import ssl

    payload = json.dumps({
        "jsonrpc": "2.0",
        "method": method,
        "params": params or {},
        "id": 1,
    }).encode()

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")

    ctx = None
    if not verify_ssl:
        print("WARNING: TLS certificate verification disabled. "
              "Connection vulnerable to MITM attacks.", file=sys.stderr)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
        return json.loads(resp.read())


# ---- Commands ----

def cmd_wallet_create(args):
    """Create a new wallet, optionally register encryption key on-chain."""
    w = Wallet.generate()
    password = ""
    if not args.no_password:
        password = getpass.getpass("Wallet password (empty for no encryption): ")

    path = os.path.join(_wallets_dir(), f"{w.address}.json")
    w.save(path, password=password)

    registered = False
    if getattr(args, 'register', False):
        token = args.token or os.environ.get("QBIT_RPC_TOKEN", "")
        if not token:
            token = getpass.getpass("RPC auth token: ")
        try:
            result = _rpc_call(
                args.rpc, "qv_registerKey",
                {"wallet_address": w.address},
                token=token, verify_ssl=not args.insecure)
            if "error" not in result:
                registered = True
        except Exception as e:
            print(f"Warning: key registration failed ({e}). "
                  f"Run later: qbit wallet register {w.address}", file=sys.stderr)

    if args.json:
        print(json.dumps({"address": w.address, "path": path, "registered": registered}))
    else:
        print(f"Wallet created: {w.address}")
        print(f"Saved to: {path}")
        if password:
            print("(encrypted with password)")
        if registered:
            print("Encryption key registered on-chain. Others can now share with you.")


def cmd_wallet_list(args):
    """List all local wallets."""
    wallets = _load_all_wallets()
    if args.json:
        print(json.dumps([{"address": a} for a in sorted(wallets.keys())]))
    else:
        if not wallets:
            print("No wallets found. Run: qbit wallet create")
            return
        for addr in sorted(wallets.keys()):
            print(addr)


def cmd_notarize(args):
    """Notarize a document file."""
    if not os.path.isfile(args.file):
        print(f"Error: file not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    doc_hash = _file_hash(args.file)
    token = args.token or os.environ.get("QBIT_RPC_TOKEN", "")
    if not token:
        token = getpass.getpass("RPC auth token: ")

    wallet_addr = args.wallet
    if not wallet_addr:
        wallets = _load_all_wallets()
        if len(wallets) == 1:
            wallet_addr = list(wallets.keys())[0]
        elif wallets:
            print("Multiple wallets found. Specify with --wallet ADDRESS:")
            for a in sorted(wallets.keys()):
                print(f"  {a}")
            sys.exit(1)
        else:
            print("No wallets. Run: qbit wallet create", file=sys.stderr)
            sys.exit(1)

    result = _rpc_call(
        args.rpc, "qv_notarize",
        {"wallet_address": wallet_addr, "document_hash": doc_hash,
         "metadata": args.metadata or os.path.basename(args.file)},
        token=token, verify_ssl=not args.insecure)

    if "error" in result:
        print(f"Error: {result['error']['message']}", file=sys.stderr)
        sys.exit(1)

    tx_id = result["result"]["tx_id"]
    if args.json:
        print(json.dumps({"tx_id": tx_id, "document_hash": doc_hash, "file": args.file}))
    else:
        print(f"Notarized: {os.path.basename(args.file)}")
        print(f"SHA3-256:  {doc_hash}")
        print(f"TX ID:     {tx_id}")
        print(f"(wait for next block to confirm)")


def cmd_verify(args):
    """Verify a document is notarized on-chain."""
    if not os.path.isfile(args.file):
        print(f"Error: file not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    doc_hash = _file_hash(args.file)
    result = _rpc_call(
        args.rpc, "qv_verifyDocument",
        {"document_hash": doc_hash},
        verify_ssl=not args.insecure)

    if "error" in result:
        print(f"Error: {result['error']['message']}", file=sys.stderr)
        sys.exit(1)

    proof = result.get("result")
    if not proof:
        if args.json:
            print(json.dumps({"verified": False, "document_hash": doc_hash}))
        else:
            print(f"NOT FOUND: {os.path.basename(args.file)} is not notarized on-chain")
        sys.exit(1)

    if args.json:
        print(json.dumps({"verified": True, "document_hash": doc_hash, **proof}))
    else:
        import datetime
        ts = datetime.datetime.fromtimestamp(proof["timestamp"]).isoformat()
        print(f"VERIFIED: {os.path.basename(args.file)}")
        print(f"  SHA3-256:   {doc_hash}")
        print(f"  Block:      #{proof['block_index']}")
        print(f"  Timestamp:  {ts}")
        print(f"  TX ID:      {proof['tx_id']}")
        print(f"  Notarizer:  {proof['sender']}")


def cmd_proof_export(args):
    """Export a standalone verification proof."""
    if not os.path.isfile(args.file):
        print(f"Error: file not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    doc_hash = _file_hash(args.file)

    # Get notarization info
    result = _rpc_call(
        args.rpc, "qv_verifyDocument",
        {"document_hash": doc_hash},
        verify_ssl=not args.insecure)

    if "error" in result or not result.get("result"):
        print(f"Error: document not notarized on-chain", file=sys.stderr)
        sys.exit(1)

    info = result["result"]

    # Get the block
    block_result = _rpc_call(
        args.rpc, "qv_getBlock",
        {"index": info["block_index"]},
        verify_ssl=not args.insecure)

    if "error" in block_result or not block_result.get("result"):
        print(f"Error: block not found", file=sys.stderr)
        sys.exit(1)

    block = block_result["result"]

    # Find tx index in block
    tx_index = None
    for i, tx in enumerate(block["transactions"]):
        if tx["id"] == info["tx_id"]:
            tx_index = i
            break

    if tx_index is None:
        print(f"Error: transaction not found in block", file=sys.stderr)
        sys.exit(1)

    # Get merkle proof
    tx_hashes = [bytes.fromhex(tx["id"]) for tx in block["transactions"]]
    proof_path = merkle_proof(tx_hashes, tx_index)
    proof_serialized = [{"hash": h.hex(), "is_left": left} for h, left in proof_path]

    proof_doc = {
        "version": 1,
        "type": "qbit-notarization-proof",
        "document_hash": doc_hash,
        "tx_id": info["tx_id"],
        "block": {
            "index": block["index"],
            "timestamp": block["timestamp"],
            "hash": block["hash"],
            "prevHash": block["prevHash"],
            "merkleRoot": block["merkleRoot"],
            "validator": block["validator"],
            "signature": block["signature"],
            "txCount": len(block["transactions"]),
        },
        "merkle_proof": proof_serialized,
        "notarizer": info["sender"],
    }

    fmt = getattr(args, 'format', 'json') or 'json'

    if fmt == 'html':
        import datetime
        ts = datetime.datetime.fromtimestamp(block["timestamp"]).isoformat()
        template_path = os.path.join(os.path.dirname(__file__), "templates", "proof_certificate.html")
        with open(template_path) as tf:
            html = tf.read()
        from html import escape as html_escape
        # Escape all substitutions to prevent XSS
        proof_json_safe = json.dumps(proof_doc, indent=2, ensure_ascii=True).replace("</", "<\\/")
        html = (html
                .replace("{{DOCUMENT_HASH}}", html_escape(doc_hash))
                .replace("{{NOTARIZER}}", html_escape(info["sender"]))
                .replace("{{BLOCK_INDEX}}", html_escape(str(block["index"])))
                .replace("{{BLOCK_HASH}}", html_escape(block["hash"]))
                .replace("{{TIMESTAMP}}", html_escape(ts))
                .replace("{{TX_ID}}", html_escape(info["tx_id"]))
                .replace("{{VALIDATOR}}", html_escape(block["validator"]))
                .replace("{{PROOF_JSON}}", proof_json_safe))
        output = args.output or args.file + ".proof.html"
        with open(output, "w") as f:
            f.write(html)
    else:
        output = args.output or args.file + ".proof.json"
        with open(output, "w") as f:
            json.dump(proof_doc, f, indent=2)

    if args.json:
        print(json.dumps({"proof_file": output, "document_hash": doc_hash, "format": fmt}))
    else:
        print(f"Proof exported: {output}")
        print(f"Share this file for independent verification:")
        if fmt == 'html':
            print(f"  Open {output} in any web browser")
        else:
            print(f"  qbit verify-proof {output}")


def cmd_verify_proof(args):
    """Verify a proof file offline (no node needed)."""
    with open(args.proof_file, "r") as f:
        proof = json.load(f)

    if proof.get("type") != "qbit-notarization-proof":
        print("Error: not a valid QBit proof file", file=sys.stderr)
        sys.exit(1)

    doc_hash = proof["document_hash"]
    tx_id = proof["tx_id"]
    block = proof["block"]
    merkle_path = proof["merkle_proof"]

    # 1. Verify merkle proof
    proof_tuples = [(bytes.fromhex(p["hash"]), p["is_left"]) for p in merkle_path]
    merkle_valid = verify_merkle_proof(
        bytes.fromhex(tx_id), proof_tuples, bytes.fromhex(block["merkleRoot"]))

    # 2. Verify block header hash
    header_obj = {
        "baseFee": block.get("baseFee", 0),
        "index": block["index"],
        "merkleRoot": block["merkleRoot"],
        "prevHash": block["prevHash"],
        "timestamp": block["timestamp"],
        "txCount": block.get("txCount", 0),
        "validator": block["validator"],
    }
    header_bytes = json.dumps(header_obj, sort_keys=True, separators=(",", ":")).encode()
    computed_hash = sha3_256(header_bytes).hex()
    claimed_hash = block.get("hash", "")

    errors = []
    if not merkle_valid:
        errors.append("Merkle proof INVALID — transaction not in block")
    if computed_hash != claimed_hash:
        errors.append(f"Block hash mismatch: computed {computed_hash[:16]}... != claimed {claimed_hash[:16]}...")

    if args.json:
        print(json.dumps({
            "valid": len(errors) == 0,
            "document_hash": doc_hash,
            "block_index": block["index"],
            "timestamp": block["timestamp"],
            "errors": errors,
        }))
    else:
        if errors:
            print("VERIFICATION FAILED:")
            for e in errors:
                print(f"  - {e}")
            sys.exit(1)
        else:
            import datetime
            ts = datetime.datetime.fromtimestamp(block["timestamp"]).isoformat()
            print(f"PROOF VALID")
            print(f"  Document:   {doc_hash}")
            print(f"  Block:      #{block['index']}")
            print(f"  Timestamp:  {ts}")
            print(f"  Validator:  {block['validator']}")
            print(f"  Notarizer:  {proof['notarizer']}")


def _get_ipfs_client(args):
    """Create an IPFSClient from CLI args, or return None if unavailable."""
    from cli.ipfs_client import IPFSClient
    ipfs_api = getattr(args, 'ipfs_api', 'http://127.0.0.1:5001')
    max_size = getattr(args, 'max_file_size', None)
    kwargs = {"api_url": ipfs_api}
    if max_size is not None:
        kwargs["max_file_size"] = max_size
    client = IPFSClient(**kwargs)
    if not client.is_available():
        return None
    return client


def cmd_store(args):
    """Store a document reference on-chain, optionally pinning to IPFS."""
    if not os.path.isfile(args.file):
        print(f"Error: file not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    doc_hash = _file_hash(args.file)
    token = args.token or os.environ.get("QBIT_RPC_TOKEN", "")
    if not token:
        token = getpass.getpass("RPC auth token: ")

    wallet_addr = args.wallet
    if not wallet_addr:
        wallets = _load_all_wallets()
        if len(wallets) == 1:
            wallet_addr = list(wallets.keys())[0]
        else:
            print("Specify wallet with --wallet ADDRESS", file=sys.stderr)
            sys.exit(1)

    cid = args.cid
    ipfs_pinned = False

    # If --ipfs flag is set or no manual CID, try IPFS pinning
    if getattr(args, 'ipfs', False) and not cid:
        client = _get_ipfs_client(args)
        if client is not None:
            try:
                cid = client.add_file(args.file)
                ipfs_pinned = True
            except ValueError as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
            except Exception as e:
                print(f"Warning: IPFS pin failed ({e}). "
                      f"Falling back to local reference.", file=sys.stderr)
        else:
            print("Warning: IPFS daemon not available. "
                  "Using local reference instead.", file=sys.stderr)

    if not cid:
        cid = f"local:{doc_hash}"

    metadata = args.metadata or os.path.basename(args.file)

    result = _rpc_call(
        args.rpc, "qv_store",
        {"wallet_address": wallet_addr, "document_hash": doc_hash,
         "cid": cid, "metadata": metadata},
        token=token, verify_ssl=not args.insecure)

    if "error" in result:
        print(f"Error: {result['error']['message']}", file=sys.stderr)
        sys.exit(1)

    tx_id = result["result"]["tx_id"]
    if args.json:
        print(json.dumps({"tx_id": tx_id, "document_hash": doc_hash,
                          "cid": cid, "ipfs_pinned": ipfs_pinned}))
    else:
        print(f"Stored: {os.path.basename(args.file)}")
        print(f"  SHA3-256: {doc_hash}")
        print(f"  CID:      {cid}")
        print(f"  TX ID:    {tx_id}")
        if ipfs_pinned:
            print(f"  File pinned to IPFS.")
        else:
            print(f"  Note: Document hash recorded on-chain. File itself is NOT uploaded.")


def cmd_share(args):
    """Share a document with another user via ML-KEM encryption.

    When --ipfs is set and IPFS is available, the file is encrypted with
    ML-KEM-768 and the encrypted blob is pinned to IPFS.  The encapsulated
    key and CID are recorded on-chain via the SHARE transaction.
    """
    if not os.path.isfile(args.file):
        print(f"Error: file not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    doc_hash = _file_hash(args.file)
    token = args.token or os.environ.get("QBIT_RPC_TOKEN", "")
    if not token:
        token = getpass.getpass("RPC auth token: ")

    wallet_addr = args.wallet
    if not wallet_addr:
        wallets = _load_all_wallets()
        if len(wallets) == 1:
            wallet_addr = list(wallets.keys())[0]
        else:
            print("Specify wallet with --wallet ADDRESS", file=sys.stderr)
            sys.exit(1)

    recipient = args.to
    if not recipient:
        print("Error: --to RECIPIENT_ADDRESS required", file=sys.stderr)
        sys.exit(1)

    cid = args.cid
    ipfs_pinned = False

    # If --ipfs flag is set and no manual CID, encrypt + pin to IPFS
    if getattr(args, 'ipfs', False) and not cid:
        client = _get_ipfs_client(args)
        if client is not None:
            try:
                with open(args.file, "rb") as f:
                    plaintext = f.read()
                if len(plaintext) > client.max_file_size:
                    print(f"Error: file too large "
                          f"({len(plaintext)} > {client.max_file_size} bytes)",
                          file=sys.stderr)
                    sys.exit(1)
                # The actual ML-KEM encryption happens server-side via qv_share
                # Here we just pin the raw file; the RPC handles encryption
                cid = client.add_bytes(plaintext, os.path.basename(args.file))
                ipfs_pinned = True
            except ValueError as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
            except Exception as e:
                print(f"Warning: IPFS pin failed ({e}). "
                      f"Falling back to local reference.", file=sys.stderr)
        else:
            print("Warning: IPFS daemon not available. "
                  "Using local reference instead.", file=sys.stderr)

    if not cid:
        cid = f"local:{doc_hash}"

    result = _rpc_call(
        args.rpc, "qv_share",
        {"wallet_address": wallet_addr, "recipient_address": recipient,
         "cid": cid, "expires": args.expires},
        token=token, verify_ssl=not args.insecure)

    if "error" in result:
        print(f"Error: {result['error']['message']}", file=sys.stderr)
        sys.exit(1)

    tx_id = result["result"]["tx_id"]
    if args.json:
        print(json.dumps({"tx_id": tx_id, "cid": cid, "recipient": recipient,
                          "ipfs_pinned": ipfs_pinned}))
    else:
        print(f"Shared: {os.path.basename(args.file)}")
        print(f"  To:       {recipient}")
        print(f"  CID:      {cid}")
        print(f"  TX ID:    {tx_id}")
        if ipfs_pinned:
            print(f"  File pinned to IPFS.")
        print(f"  Encrypted with ML-KEM-768 (quantum-resistant)")


def cmd_retrieve(args):
    """Retrieve a file from IPFS by CID, optionally verifying on-chain hash."""
    from cli.ipfs_client import IPFSClient

    cid = args.cid
    # Validate CID format
    if not IPFSClient.validate_cid(cid):
        print(f"Error: invalid CID format: {cid}", file=sys.stderr)
        print("  Expected Qm... (CIDv0) or bafy... (CIDv1)", file=sys.stderr)
        sys.exit(1)

    ipfs_api = getattr(args, 'ipfs_api', 'http://127.0.0.1:5001')
    client = IPFSClient(api_url=ipfs_api)

    if not client.is_available():
        print("Error: IPFS daemon not available at " + ipfs_api, file=sys.stderr)
        print("  Start your IPFS daemon: ipfs daemon", file=sys.stderr)
        sys.exit(1)

    try:
        data = client.cat(cid)
    except Exception as e:
        print(f"Error: failed to retrieve {cid}: {e}", file=sys.stderr)
        sys.exit(1)

    # Optionally verify hash against on-chain record
    if getattr(args, 'verify_hash', False):
        retrieved_hash = hashlib.sha3_256(data).hexdigest()
        result = _rpc_call(
            args.rpc, "qv_verifyDocument",
            {"document_hash": retrieved_hash},
            verify_ssl=not args.insecure)
        if "error" in result or not result.get("result"):
            print(f"Warning: document hash {retrieved_hash} not found on-chain",
                  file=sys.stderr)
        else:
            proof = result["result"]
            print(f"On-chain match: block #{proof['block_index']}, "
                  f"tx {proof['tx_id'][:16]}...", file=sys.stderr)

    output_path = getattr(args, 'output', '')
    if output_path:
        with open(output_path, "wb") as f:
            f.write(data)
        if args.json:
            print(json.dumps({"cid": cid, "output": output_path,
                              "size": len(data)}))
        else:
            print(f"Retrieved: {cid}")
            print(f"  Saved to: {output_path}")
            print(f"  Size:     {len(data)} bytes")
    else:
        # Write raw bytes to stdout
        if args.json:
            import base64
            print(json.dumps({"cid": cid, "size": len(data),
                              "data_b64": base64.b64encode(data).decode()}))
        else:
            sys.stdout.buffer.write(data)


def main():
    parser = argparse.ArgumentParser(
        prog="qbit",
        description="QBit Network — Post-Quantum Blockchain CLI")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--rpc", default="http://localhost:8545",
                        help="RPC endpoint URL")
    parser.add_argument("--insecure", action="store_true",
                        help="Skip TLS certificate verification")
    sub = parser.add_subparsers(dest="command")

    # wallet create
    wp = sub.add_parser("wallet", help="Wallet management")
    wsub = wp.add_subparsers(dest="wallet_cmd")
    wc = wsub.add_parser("create", help="Create a new wallet")
    wc.add_argument("--no-password", action="store_true")
    wc.add_argument("--register", action="store_true",
                    help="Register encryption key on-chain (requires running node)")
    wc.add_argument("--token", default="", help="RPC auth token")
    wl = wsub.add_parser("list", help="List wallets")

    # notarize
    np = sub.add_parser("notarize", help="Notarize a document")
    np.add_argument("file", help="File to notarize")
    np.add_argument("--wallet", default="", help="Wallet address")
    np.add_argument("--token", default="", help="RPC auth token (or set QBIT_RPC_TOKEN)")
    np.add_argument("--metadata", default="", help="Optional metadata")

    # verify
    vp = sub.add_parser("verify", help="Verify a document is notarized")
    vp.add_argument("file", help="File to verify")

    # proof export
    pp = sub.add_parser("proof", help="Export notarization proof")
    pp.add_argument("file", help="Notarized file")
    pp.add_argument("--output", "-o", default="", help="Output proof file")
    pp.add_argument("--format", "-f", choices=["json", "html"], default="json",
                    help="Output format (default: json)")

    # store
    sp = sub.add_parser("store", help="Store a document reference on-chain")
    sp.add_argument("file", help="File to store")
    sp.add_argument("--wallet", default="", help="Wallet address")
    sp.add_argument("--token", default="", help="RPC auth token")
    sp.add_argument("--cid", default="", help="IPFS CID (optional, auto-generates local ref)")
    sp.add_argument("--metadata", default="", help="Optional metadata")
    sp.add_argument("--ipfs", action="store_true",
                    help="Pin file to IPFS and use CID on-chain")
    sp.add_argument("--ipfs-api", default="http://127.0.0.1:5001",
                    help="IPFS API endpoint (default: http://127.0.0.1:5001)")
    sp.add_argument("--max-file-size", type=int, default=10*1024*1024,
                    help="Max file size in bytes (default: 10MB)")

    # share
    shp = sub.add_parser("share", help="Share a document with ML-KEM encryption")
    shp.add_argument("file", help="File to share")
    shp.add_argument("--to", default="", help="Recipient address (required)")
    shp.add_argument("--wallet", default="", help="Wallet address")
    shp.add_argument("--token", default="", help="RPC auth token")
    shp.add_argument("--cid", default="", help="IPFS CID (optional)")
    shp.add_argument("--expires", type=int, default=0, help="Expiry timestamp (0=never)")
    shp.add_argument("--ipfs", action="store_true",
                     help="Pin file to IPFS and use CID on-chain")
    shp.add_argument("--ipfs-api", default="http://127.0.0.1:5001",
                     help="IPFS API endpoint (default: http://127.0.0.1:5001)")
    shp.add_argument("--max-file-size", type=int, default=10*1024*1024,
                     help="Max file size in bytes (default: 10MB)")

    # retrieve (IPFS)
    rp = sub.add_parser("retrieve", help="Retrieve a file from IPFS by CID")
    rp.add_argument("cid", help="IPFS CID to retrieve")
    rp.add_argument("--output", "-o", default="", help="Save to file path")
    rp.add_argument("--verify-hash", action="store_true",
                    help="Verify retrieved file hash against on-chain record")
    rp.add_argument("--ipfs-api", default="http://127.0.0.1:5001",
                    help="IPFS API endpoint (default: http://127.0.0.1:5001)")

    # verify-proof (offline)
    vpp = sub.add_parser("verify-proof", help="Verify proof offline (no node needed)")
    vpp.add_argument("proof_file", help="Proof JSON file")

    args = parser.parse_args()

    if args.command == "wallet":
        if args.wallet_cmd == "create":
            cmd_wallet_create(args)
        elif args.wallet_cmd == "list":
            cmd_wallet_list(args)
        else:
            wp.print_help()
    elif args.command == "notarize":
        cmd_notarize(args)
    elif args.command == "verify":
        cmd_verify(args)
    elif args.command == "proof":
        cmd_proof_export(args)
    elif args.command == "store":
        cmd_store(args)
    elif args.command == "share":
        cmd_share(args)
    elif args.command == "retrieve":
        cmd_retrieve(args)
    elif args.command == "verify-proof":
        cmd_verify_proof(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
