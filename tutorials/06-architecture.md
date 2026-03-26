# Architecture Overview

## Layer Diagram

```
┌──────────────────────────────────────────────┐
│  Web Dashboard (NextJS :3000)                 │
│  Built-in SPA (/dashboard/)                  │
│  CLI tools (cli/qbit.py)                     │
├──────────────────────────────────────────────┤
│  JSON-RPC 2.0  |  REST /api/v1/  |  WS /ws   │
│  Bearer token auth, rate limiting, CORS       │
├──────────────────────────────────────────────┤
│  P2P Network (TCP port 9000)                  │
│  ML-DSA mutual auth + AES-256-GCM encryption  │
├──────────────────────────────────────────────┤
│  Blockchain Core                             │
│  dPoS Consensus + EIP-1559 Fees              │
│  Balance Ledger + Supply Tracking            │
├──────────────────────────────────────────────┤
│  SQLite Storage (chain.db)                   │
│  Blocks, TXs, Stakes, Epochs, Indices        │
├──────────────────────────────────────────────┤
│  PQC Crypto Layer (liboqs)                   │
│  ML-DSA-65  |  ML-KEM-768  |  SHA3-256       │
│  AES-256-GCM  |  SecureBytes                 │
└──────────────────────────────────────────────┘
```

## Design Principles

**Quantum-safe by default.** Every signature uses ML-DSA-65 (FIPS 204). Every key encapsulation uses ML-KEM-768 (FIPS 203). SHA3-256 replaces SHA-256. No classical cryptography is used anywhere in the protocol.

**Data off-chain, proof on-chain.** Documents and encrypted files are stored on IPFS. Only the SHA3-256 hash and IPFS CID appear on-chain. The chain never holds plaintext.

**Purpose-built.** There are no smart contracts. The 11 transaction types cover all supported use cases with a fixed, audited code path for each. This reduces the attack surface substantially.

## Identity Model

Each address corresponds to a wallet holding two independent keypairs:

```
Wallet
  ML-DSA-65 keypair (signing)
    public_key   1,952 bytes
    secret_key   4,032 bytes
    Signs all transactions and blocks

  ML-KEM-768 keypair (encryption)
    public_key   1,184 bytes
    secret_key   2,400 bytes
    Receives encapsulated session keys for SHARE operations

  Address = "qv1" + hex(SHA3-256(signing_public_key))
            = 67 characters (3-char prefix + 64 hex)
```

The encryption public key is registered on-chain via a `REGISTER_KEY` transaction, making it discoverable by other users who want to share encrypted files with you.

## The 11 Transaction Types

| Type | Purpose | Fee (QBIT at base_fee=10) |
|------|---------|--------------------------|
| TRANSFER | Send QBIT tokens between addresses | 0.001 |
| NOTARIZE | Record a document hash on-chain | 0.01 |
| STORE | Record a document hash + IPFS CID | 0.02 |
| SHARE | Send an ML-KEM encrypted file reference | 0.01 |
| REGISTER_KEY | Publish your ML-KEM encryption public key | 0.1 |
| REGISTER_VALIDATOR | Publish your ML-DSA validator public key | 1.0 |
| STAKE | Self-stake weight on your validator | 0.01 |
| DELEGATE | Delegate stake weight to another validator | 0.01 |
| UNSTAKE | Begin unbonding staked weight | 0.01 |
| REVOKE_KEY | Permanently revoke a signing, encryption, or validator key | 0 (free) |
| EVIDENCE | Report a validator double-signing with proof | 0 (free) |

REVOKE_KEY and EVIDENCE are free because the network benefits from their submission. EVIDENCE in particular is how the slashing mechanism works — any node can submit proof and trigger slashing.

## Block Structure

Every block contains:

```
Block
  index         sequential integer (0 = genesis)
  timestamp     unix timestamp, strictly greater than parent
  prevHash      SHA3-256 of the parent block's header
  merkleRoot    domain-separated Merkle root of transaction IDs
  validator     qv1 address of the block producer
  txCount       number of transactions
  signature     ML-DSA-65 signature over the canonical header
  transactions  list of transaction objects

block_hash = SHA3-256(canonical_header_json)
```

The canonical header is a deterministically-sorted JSON string (no whitespace). Signatures are always over the hash of this canonical form, not over the full block.

The Merkle tree uses domain separation to prevent second-preimage attacks:
- Leaf nodes: `SHA3-256(0x00 || leaf_data)`
- Internal nodes: `SHA3-256(0x01 || left || right)`

## How Consensus Works

### dPoS Selection

For each block, the producer is selected deterministically:

1. Compute `seed = SHA3-256(parent_hash:block_index)` — this is unpredictable before the parent block exists
2. Sort validators by address (deterministic ordering)
3. Build a cumulative distribution over stake weights
4. Map the seed (as an integer) to a position in the distribution
5. The validator whose cumulative range contains that position produces the block

A validator with 3x the stake of another is selected 3x as often. The selection is deterministic — all nodes compute the same result independently.

### Epoch Rotation

The validator set is frozen for 100 blocks (one epoch). This means:
- Block N uses the validator set frozen at the start of the current epoch
- Stake changes during the epoch are recorded but do not affect selection until the next epoch
- At epoch boundary: snapshot the new validator set, distribute accumulated rewards

This prevents mid-epoch manipulation of the validator set.

### PoA Fallback

When no validators have stake (early chain, test environments), the system falls back to round-robin selection among registered validators: `validator = sorted_validators[block_index % n]`. This is backward-compatible with v0.1.0 chains.

### Block Production

The node produces a block when:
1. The transaction pool is non-empty
2. The current time slot belongs to this node's validator
3. No block for this index has been received from the network

Empty blocks are never produced. The block interval is 5 seconds.

## P2P Network

Nodes connect over TCP. Each connection goes through a 4-step ML-DSA mutual authentication handshake before exchanging any chain data:

1. **hello_auth** (Initiator → Responder): challenge + self-signed proof
2. **auth_response** (Responder → Initiator): signed challenge + counter-challenge
3. **auth_confirm** (Initiator → Responder): signed counter-challenge
4. Both sides mark the connection authenticated

After authentication, an ML-KEM-768 session key is established and all subsequent messages are encrypted with AES-256-GCM.

The P2P layer handles:
- Chain sync (status broadcasts + block requests)
- Transaction propagation
- Peer discovery (`get_peers` / `peers` messages)
- Rate limiting (20 msg/s sustained, 100-message burst per peer)
- Peer reputation scoring (8 event types, score decay, auto-ban)

## Storage

When a `--data-dir` is set, all state is stored in SQLite (`chain.db`):

| Table | Contents |
|-------|---------|
| blocks | Block headers |
| transactions | All confirmed transactions |
| notarizations | Document hash → block index index |
| validator_registry | Registered validator public keys |
| key_registry | Registered ML-KEM encryption keys |
| revoked_keys | Revocation records |
| stakes | Current stake positions |
| unbonding | Pending unbonding records |
| epochs | Epoch snapshots |
| slashing_events | Slashing history |

In-memory mode (no data dir) is used for tests and ephemeral nodes. The interface is identical — the same code paths are used in both modes.

Wallet files are stored separately in `wallets/*.json`, encrypted with AES-256-GCM using a scrypt-derived key. Each file is written atomically (tempfile + os.replace) with permissions 0o600.

## File Layout

```
qbit_network/
  crypto/         PQC primitives: ML-DSA-65, ML-KEM-768, SHA3-256,
                  AES-256-GCM, SecureBytes. No business logic here.
  core/
    wallet.py     Wallet generation, encryption, address derivation
    transaction.py Transaction construction and validation
    block.py      Block construction, signing, hash computation
    blockchain.py Chain state machine, consensus, balance ledger
    consensus.py  dPoS selection, epoch management, slashing
    sqlite_store.py SQLite persistence layer
  network/
    p2p.py        Peer connections, handshake, message routing
    rpc.py        JSON-RPC 2.0 handler
    rest_api.py   REST API aiohttp sub-application
    websocket.py  WebSocket subscription manager
    rate_limiter.py Token bucket rate limiting
    tls_manager.py TLS certificate lifecycle
    reputation.py  Peer reputation scoring
  node.py         Full node orchestrator (wires everything together)
  config.py       All configuration constants

cli/
  qbit.py         CLI: wallet, notarize, verify, proof, store, share
  ipfs_client.py  IPFS HTTP client

web/
  src/app/        NextJS pages (blocks, transactions, wallets, etc.)

docs/             ARCHITECTURE.md, PROTOCOL.md, SECURITY.md, PAPER.md
tracker/          AUDIT_LOG.md, CHANGELOG.md, ISSUES.md, FEATURES.md
```

## Security Limits

| Parameter | Value |
|-----------|-------|
| Max transactions per block | 200 |
| Max block size | 5 MB |
| Max transaction payload | 8 KB |
| Max transaction pool | 10,000 |
| Max block timestamp drift | 30 seconds |
| Max peers | 50 |
| Max RPC body size | 1 MB |
| Max RPC batch size | 50 |

## Next Steps

- [REST API reference](07-rest-api.md)
- [Fee system details](08-fees.md)
- [Security model and audit history](10-security.md)
- [Protocol wire formats](../docs/PROTOCOL.md)
