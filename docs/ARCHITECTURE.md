# QBit Network Architecture

## Design Principles

1. **Quantum-safe by default** - All signatures and key exchange use NIST PQC standards
2. **Data off-chain, proof on-chain** - Only hashes/CIDs on-chain, encrypted blobs on IPFS
3. **Zero-knowledge to chain** - Chain never sees plaintext, only ML-DSA signatures and ML-KEM ciphertexts
4. **Purpose-built** - Not a general smart contract platform; optimized for document notarization and encrypted sharing

## Layer Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Application                       │
│              (QBit Network Client / CLI)             │
├─────────────────────────────────────────────────────┤
│           JSON-RPC API  |  REST API  |  WebSocket   │
│       (node.py + rpc.py + rest_api.py + websocket.py)│
│   Bearer auth | Body limits | Batch caps | Rate limit│
├─────────────┬───────────────────┬───────────────────┤
│  Blockchain │    Consensus      │     P2P Network   │
│  (SQLite +  │    (PoA round-    │   (TCP + JSON     │
│   indices)  │     robin)        │    newline-delim)  │
├─────────────┴───────────────────┴───────────────────┤
│                     Core                             │
│   Wallet | Transaction | Block | State Indices       │
├─────────────────────────────────────────────────────┤
│                    Crypto                            │
│  ML-DSA-65 | ML-KEM-768 | SHA3-256 | AES-256-GCM    │
│                   (liboqs)                           │
└─────────────────────────────────────────────────────┘
```

## Identity Model

Each wallet holds two independent PQC keypairs:

```
┌─ Wallet ──────────────────────────────────┐
│                                           │
│  ML-DSA-65 KeyPair (signing):             │
│    public_key   1,952 bytes               │
│    secret_key   4,032 bytes               │
│    → Signs transactions and blocks        │
│                                           │
│  ML-KEM-768 KeyPair (encryption):         │
│    public_key   1,184 bytes               │
│    secret_key   2,400 bytes               │
│    → Receives ML-KEM encapsulated keys    │
│                                           │
│  Address = "qv1" + SHA3-256(signing_pk)   │
│           = 67 chars (prefix + 64 hex)    │
└───────────────────────────────────────────┘
```

The encryption public key is registered on-chain via `REGISTER_KEY` transactions, enabling other users to look it up for SHARE operations.

## Transaction Types

Six transaction types are defined. All share the same wire format and validation rules; only the `payload` schema differs.

### NOTARIZE
Proves a document existed at a specific time.

```json
{
  "type": "NOTARIZE",
  "from": "qv1...",
  "payload": {
    "documentHash": "sha3_hex_of_file",
    "metadata": "optional description"
  },
  "signature": "ML-DSA signature"
}
```

### STORE
Records an encrypted vault entry with an IPFS content ID.

```json
{
  "type": "STORE",
  "from": "qv1...",
  "payload": {
    "documentHash": "sha3_hex_of_file",
    "cid": "QmIPFSContentID",
    "metadata": "encrypted description"
  }
}
```

### SHARE
Shares encrypted data using ML-KEM key encapsulation.

```
Alice → Bob flow:
1. Alice: ML-KEM.Encaps(bob_pk) → (ciphertext, shared_secret)
2. Alice: AES-GCM.Encrypt(file, key=shared_secret) → encrypted_blob
3. Alice: Upload encrypted_blob → IPFS → CID
4. Alice: SHARE tx { cid, encapsulatedKey=ciphertext } → chain
5. Bob:   Read SHARE tx from chain
6. Bob:   ML-KEM.Decaps(ciphertext, bob_sk) → shared_secret
7. Bob:   Download encrypted_blob from IPFS
8. Bob:   AES-GCM.Decrypt(blob, key=shared_secret) → file
```

### REGISTER_KEY
Binds an ML-KEM encryption public key to the sender's address on-chain.

### REGISTER_VALIDATOR
Registers an ML-DSA-65 validator public key on-chain, enabling other nodes to verify block signatures without out-of-band key distribution.

```json
{
  "type": "REGISTER_VALIDATOR",
  "from": "qv1...",
  "payload": {
    "validator_pubkey": "ML-DSA hex (1952 bytes)",
    "validator_address": "qv1... (must match derivation from validator_pubkey)"
  }
}
```

Duplicate registration (address already in registry) is rejected. The genesis validator is auto-registered in memory on `init_chain()`.

### REVOKE_KEY
Permanently revokes a signing, encryption, or validator key on-chain.

```json
{
  "type": "REVOKE_KEY",
  "from": "qv1...",
  "payload": {
    "key_type": "signing | encryption | validator",
    "reason": "compromised | rotation | decommission"
  }
}
```

Self-revocation only: the sender must be the key owner. Revoking a signing key blocks the address from submitting further transactions. Revoking a validator key removes the validator from the active set. The genesis validator cannot be revoked.

## Block Structure

```
┌─ Block ──────────────────────────────────────┐
│  index:       sequential integer              │
│  timestamp:   unix timestamp (monotonic)      │
│  prevHash:    SHA3-256 of parent header       │
│  merkleRoot:  domain-separated Merkle root    │
│  validator:   qv1 address of block producer   │
│  txCount:     number of transactions          │
│  signature:   ML-DSA signature of header      │
│  transactions: [tx1, tx2, ...]               │
│                                              │
│  block_hash = SHA3-256(canonical_header_json) │
└──────────────────────────────────────────────┘
```

## Consensus: Proof of Authority

- Validators are registered at startup with their ML-DSA public keys
- Round-robin turn selection: `validator = sorted_validators[block_index % n]`
- Block production only when transaction pool is non-empty
- Self-produced blocks are validated through consensus before appending
- Timestamp monotonicity enforced: `max(time.time(), parent.timestamp + 1)`

## Merkle Tree

Domain-separated to prevent second-preimage attacks:

```
Leaf hash:   SHA3-256(0x00 || leaf_data)
Node hash:   SHA3-256(0x01 || left || right)
Odd element: Promoted to next layer without pairing (no duplication)
```

## REST API Gateway

Mounted at `/api/v1/` alongside the JSON-RPC endpoint. Implemented as an aiohttp sub-app in `network/rest_api.py`.

### Public Endpoints (no auth required)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/info` | Node info (version, chain_id, height, peers) |
| GET | `/api/v1/health` | Liveness check |
| GET | `/api/v1/blocks` | Paginated block list (`page`, `limit`) |
| GET | `/api/v1/blocks/latest` | Most recent block |
| GET | `/api/v1/blocks/:index` | Block by index |
| GET | `/api/v1/blocks/hash/:hash` | Block by hash |
| GET | `/api/v1/txs/:txid` | Transaction by ID |
| GET | `/api/v1/txs/sender/:addr` | Transactions by sender (paginated) |
| GET | `/api/v1/address/:addr` | Address summary |
| GET | `/api/v1/notarizations/:hash` | All notarizations for document hash |
| GET | `/api/v1/validators` | Active validator list |
| GET | `/api/v1/pool` | Transaction pool (paginated) |
| GET | `/api/v1/pool/count` | Transaction pool count |
| POST | `/api/v1/verify` | Verify document hash (public read-only) |

### Protected Endpoints (Bearer token required)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/txs` | Submit raw transaction |
| POST | `/api/v1/wallets` | Create wallet |
| GET | `/api/v1/wallets` | List wallets |
| POST | `/api/v1/notarize` | Submit NOTARIZE transaction |
| POST | `/api/v1/store` | Submit STORE transaction |
| POST | `/api/v1/share` | Submit SHARE transaction |
| POST | `/api/v1/register-validator` | Submit REGISTER_VALIDATOR transaction |

Response envelope: `{"data": ..., "error": null}` on success; `{"data": null, "error": {"code": N, "message": "..."}}` on error. Pagination uses 1-based `page` with configurable `limit` (default 20, max 100). CORS middleware supports configurable origins with preflight `204` responses.

## WebSocket Subscription System

Available at `WS /ws` on the same port as the RPC server. Implemented in `network/websocket.py`.

### Channels

| Channel | Event | Payload |
|---------|-------|---------|
| `new_block` | Block appended (local production or P2P receipt) | Block dict |
| `new_tx` | Transaction submitted (RPC or P2P) | TX dict |
| `chain_stats` | Periodic broadcast every 5s | `{height, tx_count, pool_size, peers}` |

`chain_stats` broadcasts are skipped when no clients are subscribed.

### Client Protocol

```json
// Subscribe
{"action": "subscribe", "channel": "new_block"}

// Unsubscribe
{"action": "unsubscribe", "channel": "new_block"}

// Keepalive
{"action": "ping"}
// Response: {"action": "pong"}
```

Errors return `{"error": {"code": N, "message": "..."}}`.

### WebSocketManager Limits

| Parameter | Value |
|-----------|-------|
| Max concurrent connections | 100 |
| Max subscriptions per client | 10 |
| Rate limit per client | 10 msg/s |
| Heartbeat interval | 30s (aiohttp built-in) |
| Max inbound message size | 8 KB |

No authentication required; event payloads contain only public chain data.

## Rate Limiting

Token bucket rate limiting in `network/rate_limiter.py`.

### P2P Rate Limiting

- Per-peer IP: 20 msg/s sustained, 100-message burst
- HELLO and HELLO_AUTH messages are exempt
- Peers disconnected after 3 violations

### RPC Rate Limiting

- Per-client IP: 10 req/s sustained, 50-request burst
- `GET /` (health) and `GET /api/v1/info` are exempt
- Returns HTTP 429 with JSON-RPC error body on violation
- Localhost (`127.0.0.1`, `::1`) exempt in development

### Shared Parameters

- LRU cap at 10,000 tracked IPs
- Active peers excluded from LRU eviction
- Stale entries purged every 60s

## Key Revocation

On-chain revocation via `REVOKE_KEY` transactions. The revocation registry (`_revoked_keys`) is persisted in the SQLite `revoked_keys` table and loaded on startup.

### Effects by Key Type

| Key Type | Effect |
|----------|--------|
| `signing` | Address blocked from submitting transactions; `submit_tx` and consensus reject |
| `encryption` | Marked in registry; downstream consumers filter |
| `validator` | Removed from `_validator_registry` and `consensus.validators`; cannot produce blocks |

Revocations are fully rolled back during chain reorg. The genesis validator cannot be revoked.

## Persistence

- SQLite (`chain.db`): Primary storage for blocks, transactions, validator registry, revocation registry, and indices when `data_dir` is set. `_ChainProxy` provides a backward-compatible list-like interface over SQLite for code that accesses `blockchain.chain`.
- In-memory mode: Used when no `data_dir` is set (tests, ephemeral nodes); full `_chain_list` retained.
- `wallets/*.json`: Atomic write, permission 0o600, scrypt+AES-GCM encryption
- Load validation: hash chain integrity, tx signatures, block signatures (when validator known)
- Atomic load: validate all blocks in temp list before committing

## Security Limits

| Parameter | Value |
|-----------|-------|
| `MAX_TX_PER_BLOCK` | 200 |
| `MAX_BLOCK_SIZE` | 5 MB |
| `MAX_TX_PAYLOAD_SIZE` | 8 KB |
| `MAX_TX_POOL_SIZE` | 10,000 |
| `MAX_BLOCK_DRIFT` | 30 seconds |
| `MAX_PEERS` | 50 |
| `MAX_RPC_BODY` | 1 MB |
| `MAX_RPC_BATCH` | 50 |
| P2P reader limit | 10 MB |
| HELLO timeout (inbound) | 10 seconds |

## Key Sizes

| Algorithm | Public Key | Secret Key | Signature/Ciphertext |
|-----------|-----------|-----------|---------------------|
| ML-DSA-65 | 1,952 B | 4,032 B | 3,309 B |
| ML-KEM-768 | 1,184 B | 2,400 B | 1,088 B (ciphertext) |
| AES-256-GCM | - | 32 B | 12 B nonce + 16 B tag |
| SHA3-256 | - | - | 32 B |
