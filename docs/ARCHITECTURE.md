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
│  (SQLite +  │    (dPoS/PoA      │   (TCP + JSON     │
│   indices)  │     fallback)     │    newline-delim)  │
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

## Financial Layer

The QBit Network has a built-in token economy with the following properties:

### Token: QBIT

- **Name**: QBit / **Symbol**: QBIT / **Decimals**: 9 (1 QBIT = 10^9 qubits)
- **Max supply**: 21,000,000 QBIT
- **Genesis allocation**: 2,100,000 QBIT to the genesis validator

### Block Rewards

- **Initial reward**: 5 QBIT (5,000,000,000 qubits) per block
- **Halving interval**: every 2,100,000 blocks
- **Supply cap**: reward capped to remaining supply; zero after max supply reached

### Transaction Fees

QBit supports two fee models, activated by `DYNAMIC_FEE_ACTIVATION_HEIGHT`:

**Legacy fixed fees** (pre-activation): per-type fee schedule with 50/50 validator/burn split.

**EIP-1559 dynamic fees** (post-activation): base fee adjusts +/-12.5% per block based on
block weight utilization vs 50% target. Transactions specify `maxFeePerWeight` and
`maxPriorityFee`. Fee = `(baseFee + effectivePriority) * weight`. 100% to validator.

| Type | Weight | Legacy Fee (qubits) |
|------|--------|-----------|
| TRANSFER | 100,000 | 1,000,000 |
| NOTARIZE | 1,000,000 | 10,000,000 |
| STORE | 2,000,000 | 20,000,000 |
| SHARE | 1,000,000 | 10,000,000 |
| REGISTER_KEY | 10,000,000 | 100,000,000 |
| REGISTER_VALIDATOR | 100,000,000 | 1,000,000,000 |
| STAKE / DELEGATE / UNSTAKE | 1,000,000 | 10,000,000 |
| REVOKE_KEY | 0 | 0 |
| EVIDENCE | 0 | 0 |

Key constants: `MAX_BLOCK_WEIGHT=20M`, `TARGET_BLOCK_WEIGHT=10M`, `BASE_FEE_CHANGE_DENOM=8`,
`MIN_BASE_FEE=1`, `MAX_BASE_FEE=10,000`, `INITIAL_BASE_FEE=10`.

### Balance Ledger

- `_balances: dict[str, int]` tracks per-address balance in qubits
- `_credit()` / `_debit()` enforce non-negative amounts and insufficient balance checks
- `_pending_debits()` prevents double-spend by summing pending pool debits
- Supply tracked via `_total_minted` and `_total_burned` counters
- Conservation invariant: `circulating + staked + burned == total_minted`

### Epoch Reward Distribution

At each epoch boundary (every 100 blocks), accumulated block rewards are redistributed to delegators:
- Validator keeps commission (default 10%, configurable 0-100%)
- Remaining pool distributed proportionally by delegation weight
- Validator balance debited by the distributed amount to maintain supply conservation
- Distributions recorded for clean rollback during chain reorganization

### TRANSFER Validation

- Recipient must be a valid `qv1` address (67 characters: 3-char prefix + 64 hex)
- Self-transfer rejected
- Amount must be a positive integer
- Balance check includes pending pool debits (fee + amount)
- Memo limited to 256 characters

### State Trie

`StateTrie` (`qbit_network/core/state_tree.py`) is a sorted key-value Merkle trie for state commitments.

- **Keys**: `balance:{address}` and `nonce:{address}` (8-byte big-endian encoded values)
- **Leaf hash**: `SHA3-256(key_bytes + value_bytes)` sorted lexicographically by key
- **Root**: SHA3-256 binary Merkle tree over sorted leaves; empty state returns `SHA3-256(b"empty_state")`
- **Proofs**: `get_proof(key)` returns Merkle inclusion proof (sibling hashes + position)
- **Verification**: `verify_proof(key, value, proof, root)` -- static, no trie instance needed
- **Snapshots**: `snapshot()` / `restore()` for O(1) rollback; snapshots pruned beyond MAX_REORG_DEPTH

The `stateRoot` is stamped on self-produced blocks (genesis + `produce_block`) after all state mutations. Received blocks MUST carry a `state_root`; blocks with empty or mismatched `state_root` are hard-rejected (RS-2).

### Receipt System

`TransactionReceipt` (`qbit_network/core/receipt.py`) records execution results for every processed transaction.

- **Fields**: tx_id, status (`success`/`failure`), fee_paid, block_index, tx_index, events
- **receipt_hash**: SHA3-256 of canonical JSON (sorted keys, compact separators)
- **receiptsRoot**: Merkle root of receipt hashes, stamped on block header alongside stateRoot
- **Events**: typed dicts emitted per TX type (Transfer, Notarize, Store, Share, Stake, Delegate, Unstake, KeyRegistered, ValidatorRegistered, KeyRevoked, Slashed)
- **Block-level events**: BlockReward (validator, amount), EpochTransition (epoch)
- **SQLite**: `receipts` and `events` tables with indexed queries
- **Rollback**: receipts and event indices cleaned up in `_rollback_block`

### Finality

Simple finality rule: a block is finalized when subsequent blocks whose validators represent >2/3 of total stake have been built on top of it. `get_finalized_height()` returns the latest finalized block index.

### Webhook System

`WebhookManager` (`qbit_network/network/webhooks.py`) provides event-driven HTTP callbacks.

- **Registration**: URL (http/https, SSRF-protected), event types (13 valid types), HMAC secret
- **Delivery**: HTTP POST with JSON `{event, block_index, timestamp}`, HMAC-SHA256 signature in `X-QBit-Signature` header
- **Retry**: 3 attempts with exponential backoff (1s, 5s, 25s), 10s timeout per attempt
- **Lifecycle**: `active` -> `failing` -> `disabled` after 10 consecutive failures
- **Limits**: max 100 webhooks, max 20 events per webhook, max 2048-char URL
- **Security**: SSRF protection blocks private/loopback/link-local/metadata URLs

## Transaction Types

Fourteen transaction types are defined. All share the same wire format and validation rules; only the `payload` schema differs.

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

### STAKE
Self-stakes weight on own validator address. Amount must be between MIN_STAKE (1) and MAX_STAKE (1,000,000).

```json
{
  "type": "STAKE",
  "from": "qv1...",
  "payload": {
    "validator_address": "qv1...",
    "amount": 1000
  }
}
```

### DELEGATE
Delegates stake weight to any registered validator.

```json
{
  "type": "DELEGATE",
  "from": "qv1...",
  "payload": {
    "validator_address": "qv1...",
    "amount": 500
  }
}
```

### UNSTAKE
Begins unbonding stake. Effective after UNBONDING_PERIOD (100 blocks).

```json
{
  "type": "UNSTAKE",
  "from": "qv1...",
  "payload": {
    "validator_address": "qv1...",
    "amount": 500
  }
}
```

### EVIDENCE
Reports validator double-signing. Contains two ML-DSA-65 signatures over different block hashes at the same block index.

```json
{
  "type": "EVIDENCE",
  "from": "qv1...",
  "payload": {
    "validator_address": "qv1...",
    "block_index": 42,
    "block_hash_1": "...",
    "block_hash_2": "...",
    "signature_1": "ML-DSA hex",
    "signature_2": "ML-DSA hex"
  }
}
```

### ISSUE_TOKEN
Creates a new custom token on the network.

```json
{
  "type": "ISSUE_TOKEN",
  "from": "qv1...",
  "payload": {
    "name": "My Token",
    "symbol": "MTK",
    "decimals": 9,
    "max_supply": 1000000000000000000,
    "transferable": true
  }
}
```

Token ID is derived deterministically: `SHA3-256(issuer_address + symbol + nonce)[:32]`. Symbol must be unique across the network. Only the issuer can mint new units.

### MINT_TOKEN
Mints new units of an existing token. Only the token issuer can mint.

```json
{
  "type": "MINT_TOKEN",
  "from": "qv1...",
  "payload": {
    "token_id": "hex_token_id",
    "to": "qv1...",
    "amount": 1000000000
  }
}
```

Minting is capped by `max_supply` and protected against integer overflow (`_MAX_TOKEN_AMOUNT = 2^63-1`).

### TRANSFER_TOKEN
Transfers custom tokens between addresses.

```json
{
  "type": "TRANSFER_TOKEN",
  "from": "qv1...",
  "payload": {
    "token_id": "hex_token_id",
    "to": "qv1...",
    "amount": 500000000
  }
}
```

Requires the token to have `transferable: true`. Sender must have sufficient token balance. Self-transfer rejected.

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

## Consensus: Delegated Proof of Stake (dPoS)

As of v0.4.0 (finalized in v0.6.0), QBit Network uses Delegated Proof of Stake with the following properties:

### Staking Model

- **STAKE** transactions: validators self-stake weight (amount 1 to 1,000,000)
- **DELEGATE** transactions: any address delegates stake weight to a registered validator
- **UNSTAKE** transactions: begin unbonding (effective after UNBONDING_PERIOD = 100 blocks)
- Stake state tracked per-validator: `{staker_address: amount}` with `_total_stake` aggregation
- SQLite persistence: `stakes` and `unbonding` tables with full rollback support

### Validator Selection

- Stake-weighted deterministic selection: `SHA3-256(parent_hash:block_index)` as seed
- Cumulative distribution over sorted validators by address
- Automatic PoA round-robin fallback when no validators have stake (backward-compatible)

### Epoch Rotation

- Every EPOCH_LENGTH (100) blocks, the active validator set is frozen for that epoch
- Epoch snapshots stored in-memory (`_epochs` dict) and SQLite (`epochs` table)
- Stake changes during an epoch take effect at the next epoch boundary
- Consensus uses frozen epoch validators for dPoS selection within the epoch
- Epoch state correctly rolled back during chain reorganization

### Slashing

- EVIDENCE transaction type for reporting validator double-signing
- Evidence must contain two valid ML-DSA-65 signatures over different block hashes at the same index
- Slashing: reduces all stakers' positions by SLASH_PERCENTAGE (50%) proportionally
- Validator removed from active set if total stake drops below MIN_STAKE
- Slashed validators cannot receive new stake (`_slashed_validators` set)
- Duplicate evidence rejected (`_processed_evidence` set)
- SQLite `slashing_events` table for persistent slashing history with rollback support

### Legacy PoA (Fallback)

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

## P2P Encrypted Channel

After mutual authentication, P2P connections are encrypted using ML-KEM-768 + AES-256-GCM:

1. Initiator generates ML-KEM encapsulation using responder's `encryption_pk`
2. Sends `session_key` message with ciphertext and own `encryption_pk`
3. Responder decapsulates to recover shared secret
4. Both derive 32-byte AES key via `SHA3-256(shared_secret)`
5. All subsequent messages wrapped in `{"type": "encrypted", "data": ciphertext_hex}`

Backward compatible: v1 peers and peers without `encryption_pk` stay plaintext.

### Connection Deduplication

After successful authentication, duplicate connections to the same remote address are detected and resolved. Deterministic tie-breaker: node with lexicographically smaller address keeps its outbound connection.

## Peer Reputation

`PeerReputation` class in `network/reputation.py` tracks per-peer scores across 8 event types:

| Event | Score Delta |
|-------|------------|
| valid_block | +10 |
| valid_tx | +1 |
| invalid_block | -50 |
| invalid_tx | -10 |
| auth_failed | -100 |
| timeout | -5 |
| rate_limited | -20 |
| protocol_error | -30 |

Peers start at score 100. A score at or below -100 triggers a ban. Score decay (0.99x per minute) ensures old events fade over time. Banned peers are rejected on inbound connections and disconnected mid-session. Manual unban resets score to the default.

In addition to score-based reputation, the `_slashed_validators` set prevents re-staking to misbehaving validators, and HELLO_AUTH mutual authentication prevents unauthenticated peers from participating.

## Chain Pruning

`Blockchain.prune(retention)` removes block data from SQLite for blocks older than `height - retention`. The default retention is `PRUNING_RETENTION = 10000` blocks.

- `SQLiteStore.prune_blocks(before_index)` performs atomic deletion of block and transaction rows.
- All in-memory indices (notarizations, key_registry, validator_registry, stakes, epochs, slashing) are preserved; only raw block storage rows are removed.
- Thread-safe via `_db_lock`.
- No-op in in-memory mode (no `data_dir`).
- Epoch snapshots act as natural state checkpoints for the retained range.

## SecureBytes

`SecureBytes` in `crypto/secure_bytes.py` provides a ctypes-backed mutable byte buffer that can be explicitly zeroed:

- `zero()` method overwrites the buffer with zeros using `ctypes.memset`.
- Context manager (`__enter__`/`__exit__`) and `__del__` auto-zero for defense in depth.
- Full bytes-like interface: `__bytes__`, `__len__`, `hex()`, `__eq__`, `__hash__`.
- `Wallet` wraps `signing_sk` and `encryption_sk` in `SecureBytes`.
- `Wallet.close()` and `Wallet.__exit__` zero all secret key material.
- `MLDSA.sign()` and `MLKEM.decapsulate()` accept `SecureBytes` transparently.
- scrypt-derived keys and decrypted plaintext buffers are zeroed after use.
- Graceful fallback to `bytearray` with best-effort zeroing if ctypes is unavailable.

## TLS Manager

`TLSManager` in `network/tls_manager.py` handles TLS certificate lifecycle:

- Generates self-signed ECC (P-256) certificates with correct X.509 fields (CN, SAN, BasicConstraints).
- Auto-renewal: checks cert expiry against `TLS_RENEWAL_THRESHOLD_DAYS` (default 30); regenerates before expiry.
- External cert hot-reload: watches file modification times, reloads SSL context on change.
- SIGHUP handler triggers manual TLS context reload on Unix platforms.
- Atomic writes (tempfile + os.replace) for cert and key files; key files written with 0o600 permissions.
- Activated with `--tls-auto`; hostname set with `--tls-hostname`.

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
| POST | `/api/v1/stake` | Submit STAKE transaction |
| POST | `/api/v1/delegate` | Submit DELEGATE transaction |
| POST | `/api/v1/unstake` | Submit UNSTAKE transaction |
| POST | `/api/v1/evidence` | Submit EVIDENCE transaction |
| GET | `/api/v1/stakes` | All validator stakes |
| GET | `/api/v1/stakes/:validator` | Specific validator stake info |
| GET | `/api/v1/epochs/current` | Current epoch info |
| GET | `/api/v1/slashing-events` | Slashing event history |

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
