# QBit Network - Post-Quantum Cryptography Blockchain

A document notarization, encrypted storage, and secure sharing blockchain built on NIST-standardized post-quantum cryptographic algorithms.

## What is QBit Network?

QBit Network is a purpose-built blockchain for **document proof** and **encrypted data sharing** that replaces all quantum-vulnerable cryptography (ECDSA, ECDH, secp256k1) with post-quantum alternatives:

| Classical | QBit Network (PQC) |
|-----------|-------------|
| ECDSA signatures | **ML-DSA-65** (CRYSTALS-Dilithium) |
| ECDH key exchange | **ML-KEM-768** (CRYSTALS-Kyber) |
| SHA-256 hashing | **SHA-3 / SHAKE-256** |
| secp256k1 keypairs | **Dual ML-DSA + ML-KEM keypairs** |

## Features

- **NOTARIZE** - Timestamp-prove a document exists on-chain (hash only, data stays off-chain)
- **STORE** - Record encrypted vault entries with IPFS CID references
- **SHARE** - Share encrypted data using ML-KEM key encapsulation (recipient decapsulates)
- **REGISTER_KEY** - Bind ML-KEM encryption public key to your address on-chain
- **Proof of Authority** consensus with round-robin validator selection
- **Merkle proofs** with domain-separated hashing (second-preimage resistant)
- **JSON-RPC 2.0** API with bearer token authentication
- **P2P networking** with peer discovery, chain sync, and SSRF protection
- **Wallet encryption** with scrypt + AES-256-GCM

## Quick Start

### Prerequisites

```bash
# macOS
brew install liboqs    # or build from source with -DBUILD_SHARED_LIBS=ON
pip3 install liboqs-python cryptography aiohttp

# Verify PQC works
python3 -c "import oqs; print('OK')"
```

### Run a Validator Node

```bash
python3 run_node.py
```

This generates a fresh validator wallet, creates a genesis block, and starts:
- P2P listener on port `9000`
- JSON-RPC API on port `8545`

The RPC auth token is printed at startup. Save it for authenticated calls.

### Run with Options

```bash
# Custom ports and data directory
python3 run_node.py --p2p-port 9001 --rpc-port 8546 --data-dir ./mychain

# Connect to existing peers
python3 run_node.py --peers 10.0.0.2:9000 10.0.0.3:9000

# Use a saved wallet
python3 run_node.py --wallet ./validator.json

# Non-validator observer node
python3 run_node.py --no-validate

# Set auth token explicitly
python3 run_node.py --rpc-token "my-secret-token"
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `QVAULT_DATA_DIR` | `~/.qvault` | Chain and wallet storage |
| `QVAULT_ALLOW_PRIVATE_PEERS` | `false` | Allow P2P connections to RFC 1918 addresses |

## RPC API

### Public Methods (no auth required)

```bash
# Chain info
curl -X POST http://localhost:8545 \
  -d '{"jsonrpc":"2.0","method":"qv_blockNumber","id":1}'

curl -X POST http://localhost:8545 \
  -d '{"jsonrpc":"2.0","method":"qv_getBlock","params":{"index":0},"id":1}'

curl -X POST http://localhost:8545 \
  -d '{"jsonrpc":"2.0","method":"qv_getTransaction","params":{"tx_id":"abc..."},"id":1}'

# Verify a document was notarized
curl -X POST http://localhost:8545 \
  -d '{"jsonrpc":"2.0","method":"qv_verifyDocument","params":{"document_hash":"deadbeef"},"id":1}'

# Lookup encryption public key
curl -X POST http://localhost:8545 \
  -d '{"jsonrpc":"2.0","method":"qv_getEncryptionPk","params":{"address":"qv1..."},"id":1}'

# Node and network info
curl -X POST http://localhost:8545 \
  -d '{"jsonrpc":"2.0","method":"qv_nodeInfo","id":1}'
```

### Protected Methods (require `Authorization: Bearer <token>`)

```bash
TOKEN="your-auth-token"

# Create wallet
curl -X POST http://localhost:8545 \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"jsonrpc":"2.0","method":"qv_newWallet","id":1}'

# Register encryption key on-chain
curl -X POST http://localhost:8545 \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"jsonrpc":"2.0","method":"qv_registerKey","params":{"wallet_address":"qv1..."},"id":1}'

# Notarize a document
curl -X POST http://localhost:8545 \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"jsonrpc":"2.0","method":"qv_notarize","params":{
    "wallet_address":"qv1...",
    "document_hash":"sha3_hex_of_your_file",
    "metadata":"optional description"
  },"id":1}'

# Store a vault entry
curl -X POST http://localhost:8545 \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"jsonrpc":"2.0","method":"qv_store","params":{
    "wallet_address":"qv1...",
    "document_hash":"sha3_hex",
    "cid":"QmIPFSContentID",
    "metadata":"encrypted description"
  },"id":1}'

# Share encrypted data (ML-KEM encapsulation)
curl -X POST http://localhost:8545 \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"jsonrpc":"2.0","method":"qv_share","params":{
    "wallet_address":"qv1_sender...",
    "recipient_address":"qv1_recipient...",
    "cid":"QmSharedFileID",
    "expires":1735689600
  },"id":1}'

# Recipient decapsulates shared secret
curl -X POST http://localhost:8545 \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"jsonrpc":"2.0","method":"qv_decapsulateShared","params":{
    "wallet_address":"qv1_recipient...",
    "tx_id":"share_tx_id..."
  },"id":1}'
```

### Full Method List

| Method | Auth | Description |
|--------|------|-------------|
| `qv_blockNumber` | No | Current chain height |
| `qv_getBlock` | No | Get block by index or hash |
| `qv_getTransaction` | No | Get transaction by ID |
| `qv_pendingTxCount` | No | Pending transaction pool size |
| `qv_verifyDocument` | No | Check if document hash is notarized |
| `qv_getEncryptionPk` | No | Lookup on-chain encryption key |
| `qv_peerCount` | No | Connected P2P peers |
| `qv_nodeInfo` | No | Node version, height, peers, wallets |
| `qv_validators` | No | List of validator addresses |
| `qv_getTxsBySender` | No | Transaction IDs by sender |
| `qv_getTxsByRecipient` | No | Transaction IDs by recipient |
| `qv_newWallet` | **Yes** | Generate new PQC wallet |
| `qv_listWallets` | **Yes** | List local wallet addresses |
| `qv_getWalletKeys` | **Yes** | Get wallet public keys |
| `qv_registerKey` | **Yes** | Register encryption key on-chain |
| `qv_notarize` | **Yes** | Notarize a document hash |
| `qv_store` | **Yes** | Store a vault entry |
| `qv_share` | **Yes** | Share data with ML-KEM encapsulation |
| `qv_getSharedWithMe` | **Yes** | Get active shares for an address |
| `qv_getSharedSecret` | **Yes** | Retrieve locally-stored shared secret |
| `qv_decapsulateShared` | **Yes** | Recipient decapsulates SHARE tx |
| `qv_sendRawTransaction` | **Yes** | Submit a pre-signed transaction |

## Project Structure

```
pqc-blockchain/
├── qbit_network/
│   ├── crypto/
│   │   ├── mldsa.py        # ML-DSA-65 signatures (liboqs)
│   │   ├── mlkem.py         # ML-KEM-768 key encapsulation (liboqs)
│   │   ├── hashing.py       # SHA3-256, SHAKE-256, Merkle tree
│   │   └── aes.py           # AES-256-GCM authenticated encryption
│   ├── core/
│   │   ├── wallet.py        # Dual-keypair wallet, scrypt+AES encryption
│   │   ├── transaction.py   # NOTARIZE, STORE, SHARE, REGISTER_KEY
│   │   ├── block.py         # Block with Merkle root and ML-DSA signature
│   │   ├── blockchain.py    # Chain state, indices, persistence
│   │   └── consensus.py     # Proof of Authority with round-robin
│   ├── network/
│   │   ├── p2p.py           # TCP P2P with peer validation
│   │   └── rpc.py           # JSON-RPC 2.0 with bearer auth
│   ├── node.py              # Full node orchestrator
│   └── config.py            # Configuration constants
├── docs/                    # Architecture and protocol documentation
├── tracker/                 # Audit logs, issue tracking, changelogs
├── run_node.py              # CLI entry point
└── requirements.txt
```

## Security

This codebase has been through **9 rounds of security audit** covering:
- Cryptographic correctness (PQC primitive usage, key handling, side-channels)
- Input validation (deserialization, RPC params, P2P messages)
- Protocol security (replay, SSRF, chain-split, genesis injection)
- Resource exhaustion (DoS vectors, memory limits, pool caps)
- Concurrency (nonce races, async lock safety)
- Persistence (atomic writes, load validation, tamper detection)

See [`tracker/AUDIT_LOG.md`](tracker/AUDIT_LOG.md) for the full 104-issue audit trail.

## License

MIT
