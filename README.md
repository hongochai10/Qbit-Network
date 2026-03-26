# QBit Network — Post-Quantum Cryptography Blockchain

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue) ![License MIT](https://img.shields.io/badge/license-MIT-green) ![Tests 1264](https://img.shields.io/badge/tests-1264-brightgreen)

QBit Network is a purpose-built blockchain for document notarization and encrypted data sharing. It replaces all quantum-vulnerable cryptography (ECDSA, ECDH, secp256k1) with NIST-standardized post-quantum alternatives, providing a quantum-resistant foundation for long-lived document proofs and confidential file exchange.

| Classical | QBit Network (PQC) |
|-----------|-------------------|
| ECDSA signatures | **ML-DSA-65** (CRYSTALS-Dilithium, FIPS 204) |
| ECDH key exchange | **ML-KEM-768** (CRYSTALS-Kyber, FIPS 203) |
| SHA-256 hashing | **SHA-3 / SHAKE-256** |
| secp256k1 keypairs | **Dual ML-DSA + ML-KEM keypairs** |

## Features

- **11 transaction types**: NOTARIZE, STORE, SHARE, REGISTER_KEY, REGISTER_VALIDATOR, REVOKE_KEY, STAKE, DELEGATE, UNSTAKE, EVIDENCE, TRANSFER
- **Delegated Proof of Stake** — stake-weighted validator selection, epoch rotation (every 100 blocks), double-sign slashing
- **ML-KEM-768 encrypted P2P** — all post-authentication peer messages encrypted with AES-256-GCM
- **3-step ML-DSA mutual auth** — P2P challenge-response handshake with verify-before-sign
- **REST API** — 36 endpoints at `/api/v1/` with pagination and CORS
- **WebSocket subscriptions** — real-time `new_block`, `new_tx`, `chain_stats` events at `/ws`
- **Web dashboard** — single-file SPA at `/dashboard/` with block explorer, validator panel, staking panel, document verifier
- **IPFS integration** — CLI store/share/retrieve with automatic pinning; CIDv0 and CIDv1 support
- **Key revocation** — permanent on-chain revocation for signing, encryption, and validator keys
- **SecureBytes** — ctypes-backed key material zeroing, explicit `zero()` on wallet close
- **TLS auto-provisioning** — `--tls-auto` generates and renews self-signed certificates; hot-reload on SIGHUP
- **Peer reputation scoring** — 8 event types, score decay, automatic banning
- **Chain pruning** — SQLite-level block removal with indices preserved
- **QBIT token economy** -- 1B max supply, 5 QBIT block reward with halving, fee burn (50%), epoch delegator rewards
- **TRANSFER transactions** -- peer-to-peer token transfers with balance checks, pending debit tracking, recipient format validation
- **16 audit rounds, 0 open issues**

## Quick Start

### Prerequisites

```bash
# macOS — build liboqs with shared library support
cd /tmp && git clone --depth 1 --branch 0.15.0 https://github.com/open-quantum-safe/liboqs.git
cd liboqs && mkdir build && cd build
cmake -DBUILD_SHARED_LIBS=ON -DCMAKE_INSTALL_PREFIX=$HOME/_oqs ..
make -j$(sysctl -n hw.ncpu) && make install

pip3 install liboqs-python cryptography aiohttp

# Verify
python3 -c "import oqs; print('OK')"
```

### Run a Validator Node

```bash
python3 run_node.py
```

Generates a validator wallet, creates a genesis block, and starts:
- P2P listener on port `9000`
- JSON-RPC + REST API + WebSocket on port `8545`
- Web dashboard at `http://localhost:8545/dashboard/`

The RPC auth token is printed at startup.

### Run with Options

```bash
# Custom ports and data directory
python3 run_node.py --p2p-port 9001 --rpc-port 8546 --data-dir ./mychain

# Connect to existing peers
python3 run_node.py --peers 10.0.0.2:9000 10.0.0.3:9000

# Use a saved wallet
python3 run_node.py --wallet ./validator.json

# Observer node (no block production)
python3 run_node.py --no-validate

# TLS with auto-generated certificate
python3 run_node.py --tls-auto --tls-hostname mynode.example.com
```

### Docker Quickstart (3-validator testnet)

```bash
docker-compose up -d
# Nodes: http://localhost:8545, :8546, :8547
curl http://localhost:8545 -d '{"jsonrpc":"2.0","method":"qv_nodeInfo","id":1}'
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `QVAULT_DATA_DIR` | `~/.qvault` | Chain and wallet storage |
| `QVAULT_ALLOW_PRIVATE_PEERS` | `false` | Allow P2P connections to RFC 1918 addresses |

## CLI Usage

```bash
# Wallet management
python3 cli/qbit.py wallet create
python3 cli/qbit.py wallet create --register --token TOKEN
python3 cli/qbit.py wallet list

# Notarize and verify
python3 cli/qbit.py notarize contract.pdf --token TOKEN
python3 cli/qbit.py verify contract.pdf

# Export proof (JSON or HTML certificate)
python3 cli/qbit.py proof contract.pdf --format html
python3 cli/qbit.py verify-proof contract.pdf.proof.json

# Store and share (encrypted, with IPFS)
python3 cli/qbit.py store document.pdf --ipfs --token TOKEN
python3 cli/qbit.py share document.pdf --to qv1recipient... --ipfs --token TOKEN
python3 cli/qbit.py retrieve QmCID --output ./retrieved.pdf
```

## API Overview

### JSON-RPC 2.0

Endpoint: `POST http://localhost:8545`

**Public methods (no auth):**

| Method | Description |
|--------|-------------|
| `qv_blockNumber` | Current chain height |
| `qv_getBlock` | Block by index or hash |
| `qv_getTransaction` | Transaction by ID |
| `qv_pendingTxCount` | Pool size |
| `qv_verifyDocument` | Check if document hash is notarized |
| `qv_getEncryptionPk` | Lookup on-chain encryption key |
| `qv_peerCount` | Connected peers |
| `qv_nodeInfo` | Node version, height, peers, wallets |
| `qv_validators` | Active validator list |
| `qv_getTxsBySender` | Transaction IDs by sender |
| `qv_getTxsByRecipient` | Transaction IDs by recipient |

**Protected methods (require `Authorization: Bearer <token>`):**

| Method | Description |
|--------|-------------|
| `qv_newWallet` | Generate new PQC wallet |
| `qv_listWallets` | List local wallet addresses |
| `qv_getWalletKeys` | Get wallet public keys |
| `qv_registerKey` | Register encryption key on-chain |
| `qv_registerValidator` | Register validator key on-chain |
| `qv_notarize` | Notarize a document hash |
| `qv_store` | Store a vault entry |
| `qv_share` | Share data with ML-KEM encapsulation |
| `qv_decapsulateShared` | Recipient decapsulates SHARE tx |
| `qv_revokeKey` | Permanently revoke a key on-chain |
| `qv_stake` / `qv_delegate` / `qv_unstake` | dPoS stake management |
| `qv_getSharedWithMe` | Get active shares for an address |
| `qv_sendRawTransaction` | Submit a pre-signed transaction |

### REST API

Base path: `GET/POST /api/v1/`

36 endpoints covering blocks, transactions, addresses, validators, stakes, epochs, slashing events, and wallet operations. Public endpoints require no auth; write endpoints require Bearer token.

```bash
# Examples
curl http://localhost:8545/api/v1/info
curl http://localhost:8545/api/v1/blocks/latest
curl http://localhost:8545/api/v1/txs/TX_ID
curl http://localhost:8545/api/v1/validators
curl http://localhost:8545/api/v1/epochs/current
curl -X POST http://localhost:8545/api/v1/verify -d '{"document_hash":"deadbeef..."}'
```

### WebSocket

Endpoint: `WS ws://localhost:8545/ws`

```json
{"action": "subscribe", "channel": "new_block"}
{"action": "subscribe", "channel": "new_tx"}
{"action": "subscribe", "channel": "chain_stats"}
{"action": "ping"}
```

Events: `new_block`, `new_tx`, `chain_stats` (every 5s). Max 100 connections, 10 subscriptions/client, 10 msg/s rate limit.

## Web Dashboard

Available at `http://localhost:8545/dashboard/` — a self-contained SPA with no external dependencies:

- **Live Stats Bar**: chain height, total transactions, pending pool, validator count, avg block time
- **Block Explorer**: paginated block list with click-to-expand detail and transaction listing
- **Transaction Viewer**: search by TX ID with type-specific payload display
- **Validator Panel**: registered validators with stake weight and slashed indicators
- **Document Verifier**: SHA3-256 hash verification via REST `/verify`
- **Staking Panel**: validator stakes, top stakers, epoch info, slashing events
- **Pool Monitor**: pending transaction count by type
- Real-time updates via WebSocket with auto-reconnect and exponential backoff

## Architecture Overview

```
┌──────────────────────────────────────────────┐
│  CLI / Web Dashboard / External Clients       │
├──────────────────────────────────────────────┤
│  JSON-RPC 2.0  |  REST /api/v1/  |  WS /ws   │
│  Bearer auth, body limits, rate limiting      │
├────────────────┬────────────────┬─────────────┤
│  Blockchain    │  Consensus     │  P2P        │
│  (SQLite)      │  (dPoS/PoA)   │  (TCP+auth) │
├────────────────┴────────────────┴─────────────┤
│  Core: Wallet | Transaction | Block | Indices  │
├──────────────────────────────────────────────┤
│  Crypto: ML-DSA-65 | ML-KEM-768 | SHA3-256    │
│          AES-256-GCM | SecureBytes  (liboqs)  │
└──────────────────────────────────────────────┘
```

**File layout:**

```
qbit_network/
├── crypto/          ML-DSA-65, ML-KEM-768, SHA3, AES-256-GCM, SecureBytes
├── core/            Wallet, Transaction, Block, Blockchain, Consensus, SQLiteStore
├── network/         P2P, JSON-RPC, REST API, WebSocket, Rate Limiter, TLS Manager, Reputation
├── node.py          Full node orchestrator
└── config.py        All configuration constants
cli/                 qbit CLI tool + IPFS client
docs/                ARCHITECTURE.md, PROTOCOL.md, SECURITY.md, PAPER.md
tracker/             AUDIT_LOG.md, ISSUES.md, FEATURES.md, CHANGELOG.md, DEVELOPMENT.md
```

## Security

QBit Network has completed **15 rounds of security audit** covering:

- Cryptographic correctness (PQC primitive usage, key handling, side-channels)
- Input validation (deserialization, RPC params, P2P messages)
- Protocol security (replay, SSRF, chain-split, genesis injection, identity confusion)
- Resource exhaustion (DoS vectors, memory limits, pool caps, rate limiting)
- Concurrency (nonce races, async lock safety)
- Persistence (atomic writes, load validation, tamper detection)
- dPoS security (slashing, epoch manipulation, evidence replay)

**0 open issues.** See [`tracker/AUDIT_LOG.md`](tracker/AUDIT_LOG.md) for the complete audit trail and [`docs/SECURITY.md`](docs/SECURITY.md) for the threat model.

**PQC algorithms:**

| Algorithm | Standard | Security Level | Use |
|-----------|----------|---------------|-----|
| ML-DSA-65 | FIPS 204 | NIST Level 3 | Transaction and block signing, P2P auth |
| ML-KEM-768 | FIPS 203 | NIST Level 3 | Key encapsulation, P2P session keys |
| SHA3-256 | FIPS 202 | 128-bit quantum | Hashing, address derivation, Merkle tree |
| AES-256-GCM | FIPS 197 | 128-bit quantum | Symmetric encryption, P2P transport |

## Documentation

| File | Contents |
|------|---------|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Layer diagram, identity model, all 10 TX types, dPoS, WebSocket, REST API, security limits |
| [`docs/PROTOCOL.md`](docs/PROTOCOL.md) | Wire formats, consensus rules, P2P handshake spec, WebSocket protocol |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Threat model, audit history, all security controls |
| [`docs/PAPER.md`](docs/PAPER.md) | Academic paper with benchmarks and formal analysis |
| [`tracker/CHANGELOG.md`](tracker/CHANGELOG.md) | Release notes v0.1.0 through v0.4.0 |
| [`tracker/DEVELOPMENT.md`](tracker/DEVELOPMENT.md) | Setup guide, code conventions, configuration |

## Contributing

1. Read [`tracker/DEVELOPMENT.md`](tracker/DEVELOPMENT.md) for setup and code conventions.
2. Run the full test suite before submitting: `pytest tests/`
3. Security issues: run through the audit checklist in [`docs/SECURITY.md`](docs/SECURITY.md).

## License

MIT
