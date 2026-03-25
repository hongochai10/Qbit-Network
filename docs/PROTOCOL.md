# QBit Network Protocol Specification

## 1. Address Derivation

```
address = "qv1" + hex(SHA3-256(ML-DSA-65 public key))
```

- Length: 67 characters (3 prefix + 64 hex)
- Deterministic: same public key always produces same address
- One-way: cannot recover public key from address

## 2. Transaction Format

### Wire Format (JSON)

```json
{
  "id": "SHA3-256 hex of signable content",
  "type": "NOTARIZE | STORE | SHARE | REGISTER_KEY | REGISTER_VALIDATOR | REVOKE_KEY | STAKE | DELEGATE | UNSTAKE | EVIDENCE",
  "from": "qv1...",
  "to": "qv1... (recipient, required for SHARE)",
  "timestamp": 1700000000,
  "nonce": 0,
  "chainId": "qbit-mainnet",
  "payload": { ... },
  "signature": "ML-DSA hex",
  "sender_pubkey": "ML-DSA public key hex (1952 bytes)"
}
```

### Signable Bytes (Canonical)

```python
json.dumps({
    "chainId": chain_id,
    "from": sender,
    "nonce": nonce,
    "payload": payload,
    "timestamp": timestamp,
    "to": recipient,
    "type": tx_type,
}, sort_keys=True, separators=(',',':'))
```

### Transaction ID

```
tx_id = hex(SHA3-256(signable_bytes))
```

### Payload Schemas

| Type | Required Keys | Optional Keys |
|------|--------------|---------------|
| `NOTARIZE` | `documentHash` (hex) | `metadata` |
| `STORE` | `documentHash` (hex), `cid` | `metadata` |
| `SHARE` | `cid`, `encapsulatedKey` (hex) | `expires` (int >= 0) |
| `REGISTER_KEY` | `encryption_pk` (hex) | - |
| `REGISTER_VALIDATOR` | `validator_pubkey` (hex, 1952 bytes), `validator_address` (qv1...) | - |
| `REVOKE_KEY` | `key_type` (`signing`\|`encryption`\|`validator`), `reason` (`compromised`\|`rotation`\|`decommission`) | - |
| `STAKE` | `validator_address` (qv1...), `amount` (int, 1-1,000,000) | - |
| `DELEGATE` | `validator_address` (qv1...), `amount` (int, 1-1,000,000) | - |
| `UNSTAKE` | `validator_address` (qv1...), `amount` (int, 1-1,000,000) | - |
| `EVIDENCE` | `validator_address` (qv1...), `block_index` (int), `block_hash_1` (hex), `block_hash_2` (hex), `signature_1` (ML-DSA hex), `signature_2` (ML-DSA hex) | - |

No extra keys allowed (enforced by `_ALLOWED_KEYS` whitelist).

#### REGISTER_VALIDATOR Rules

- `validator_pubkey` must be exactly 1952 bytes when decoded.
- `validator_address` must equal `"qv1" + hex(SHA3-256(validator_pubkey))`.
- The `from` field must match `validator_address` (self-registration only).
- If the address is already in the validator registry, the transaction is rejected.

#### REVOKE_KEY Rules

- The `from` field must be the address of the key being revoked (self-revocation only).
- `key_type` must be one of: `signing`, `encryption`, `validator`.
- `reason` must be one of: `compromised`, `rotation`, `decommission`.
- Revoking an already-revoked key is rejected (idempotency guard).
- The genesis validator's signing key cannot be revoked.

#### STAKE Rules

- `validator_address` must be the sender's own address (self-stake only).
- `amount` must be an integer in [MIN_STAKE (1), MAX_STAKE (1,000,000)].
- The target validator must be registered in `_validator_registry`.
- The target validator must not be slashed.

#### DELEGATE Rules

- `validator_address` must be a registered validator.
- `amount` must be an integer in [MIN_STAKE (1), MAX_STAKE (1,000,000)].
- The target validator must not be slashed.

#### UNSTAKE Rules

- `validator_address` must be a registered validator.
- `amount` must be an integer in [MIN_STAKE (1), MAX_STAKE (1,000,000)].
- The sender must have at least `amount` staked with the target validator.
- Initiates unbonding; stake returned after UNBONDING_PERIOD (100 blocks).

#### EVIDENCE Rules

- `validator_address` must be a registered validator.
- `block_hash_1` and `block_hash_2` must be different (proves double-signing).
- Both `signature_1` and `signature_2` must be valid ML-DSA-65 signatures from the validator's pubkey.
- The validator must not have been previously slashed (no duplicate evidence processing).
- EVIDENCE payload uses 32KB size limit (accommodates two ML-DSA-65 signatures).

### Validation Rules

- Signature must verify against `sender_pubkey`
- `sender_pubkey` must derive to `from` address
- `sender_pubkey` must be exactly 1952 bytes
- Payload must not exceed 8 KB serialized
- Timestamp must be within [-24h, +5min] of current time
- Nonce must equal `last_confirmed_nonce + 1 + pending_count`
- SHARE transactions must have non-empty `to` field

## 3. Block Format

### Wire Format (JSON)

```json
{
  "hash": "SHA3-256 hex of header",
  "index": 0,
  "timestamp": 1700000000,
  "prevHash": "0000...0000 (64 hex chars for genesis)",
  "merkleRoot": "hex of Merkle root of tx_ids",
  "validator": "qv1...",
  "transactions": [ ... ],
  "signature": "ML-DSA hex"
}
```

### Header Bytes (Canonical)

```python
json.dumps({
    "index": index,
    "merkleRoot": merkle_root,
    "prevHash": prev_hash,
    "timestamp": timestamp,
    "txCount": len(transactions),
    "validator": validator,
}, sort_keys=True, separators=(',',':'))
```

### Block Hash

```
block_hash = hex(SHA3-256(header_bytes))
```

### Consensus Rules

1. Genesis (index=0): Accepted if matches locked genesis hash (or hash not yet locked)
2. Non-genesis:
   - `index == len(chain)` (next expected)
   - `prev_hash == parent.block_hash`
   - `timestamp > parent.timestamp`
   - `timestamp <= now + 30s`
   - `validator` must be registered
   - Validator selection (dPoS or PoA fallback):
     - **dPoS mode** (when validators have stake): stake-weighted deterministic selection using `SHA3-256(parent_hash:block_index)` as seed
     - **PoA fallback** (no stake): `validator == sorted_validators[index % n]` (round-robin)
   - Within an epoch, the frozen validator set is used for selection
   - Block signature valid against validator's ML-DSA public key
   - At least 1 transaction (no empty blocks)
   - Max 200 transactions
   - No duplicate tx_ids within block
   - No tx_ids already in chain (cross-block replay prevention)
   - All tx signatures valid
   - All tx payloads valid
   - Nonces sequential per sender within block
   - First nonce per sender matches chain state
   - EVIDENCE transactions verified: both signatures valid, different block hashes, same index

## 4. P2P Protocol

### Transport

TCP with newline-delimited JSON messages. Reader limit: 10 MB.

### Message Types

| Type | Direction | Description | Since |
|------|-----------|-------------|-------|
| `hello` | Bidirectional | Unauthenticated handshake (v1 peers) | v0.1.0 |
| `hello_auth` | Initiator→Responder | Authenticated handshake step 1 (ML-DSA challenge) | v0.2.1 |
| `auth_response` | Responder→Initiator | Authenticated handshake step 2 (signed challenge + counter-challenge) | v0.2.1 |
| `auth_confirm` | Initiator→Responder | Authenticated handshake step 3 (signed counter-challenge) | v0.2.1 |
| `new_block` | Broadcast | New block announcement (requires auth for v2 peers) | v0.1.0 |
| `new_tx` | Broadcast | New transaction announcement (requires auth for v2 peers) | v0.1.0 |
| `get_blocks` | Request | Request blocks with request_id (requires auth for v2 peers) | v0.2.0 |
| `blocks` | Response | Block data response with request_id (requires auth for v2 peers) | v0.2.0 |
| `get_peers` | Request | Request peer list | v0.1.0 |
| `peers` | Response | Peer address list | v0.1.0 |
| `status` | Broadcast | Chain height announcement | v0.2.0 |

### Protocol Versioning

| Version | Release | Features |
|---------|---------|----------|
| 1 | v0.1.0-v0.2.0 | Plain hello, no auth, no request-ID |
| 2 | v0.2.1+ | hello_auth challenge-response, request-ID correlation |

Negotiation: `min(initiator_version, responder_version)`.

### Authenticated Handshake (v0.2.1+)

Full 3-step mutual authentication using ML-DSA-65 challenge-response:

#### Step 1 — hello_auth (Initiator → Responder)

```json
{
  "type": "hello_auth",
  "protocol_version": 2,
  "node_id": "qv1...",
  "port": 9000,
  "chain_id": "qbit-mainnet",
  "challenge": "<32 bytes hex, os.urandom>",
  "timestamp": 1700000000,
  "signing_pk": "<ML-DSA-65 public key hex, 1952 bytes>",
  "proof": "<ML-DSA sig over AUTH_DOMAIN || challenge || initiator_address>"
}
```

The `proof` field (added in v0.4.0) resolves SPRINT1-003: the responder verifies the initiator's proof before signing anything, preventing identity confusion attacks.

#### Step 2 — auth_response (Responder → Initiator)

```json
{
  "type": "auth_response",
  "protocol_version": 2,
  "node_id": "qv1...",
  "port": 9000,
  "chain_id": "qbit-mainnet",
  "challenge_sig": "<ML-DSA sig over initiator's challenge>",
  "counter_challenge": "<32 bytes hex, os.urandom>",
  "signing_pk": "<ML-DSA-65 public key hex, 1952 bytes>"
}
```

Responder validates `timestamp` within MAX_BLOCK_DRIFT (30s), verifies `chain_id`, derives `node_id` from `signing_pk`, signs the initiator's `challenge`.

#### Step 3 — auth_confirm (Initiator → Responder)

```json
{
  "type": "auth_confirm",
  "challenge_sig": "<ML-DSA sig over responder's counter_challenge>"
}
```

Both sides mark `peer.authenticated = True` after verifying the other's signature.

#### Signature Domain

```
ML-DSA.Sign(sk, "QBIT_AUTH_v2:" || peer_challenge || own_address)
```

Domain prefix `QBIT_AUTH_v2:` prevents cross-protocol signature reuse. Challenges are 32-byte random values (`os.urandom(32)`), single-use (cleared before verification). Auth deadline tracked with `time.monotonic()` to prevent clock-skew bypass.

#### Auth Gating

After the grace period expires, `new_block`, `new_tx`, `get_blocks`, and `blocks` messages from unauthenticated v2 peers are rejected. A failed challenge-response triggers disconnect with no v1 fallback (no downgrade to unauthenticated state on auth failure).

### Encrypted Channel (v0.4.0+)

After mutual authentication completes, the initiator establishes an encrypted channel:

1. Initiator sends `session_key` message containing ML-KEM-768 ciphertext (encapsulated using responder's `encryption_pk`) and initiator's `encryption_pk`
2. Responder decapsulates the ciphertext using its ML-KEM secret key to recover the shared secret
3. Both sides derive AES-256-GCM key: `key = SHA3-256(shared_secret)`
4. All subsequent messages are wrapped: `{"type": "encrypted", "data": "<AES-GCM ciphertext hex>"}`
5. AES-GCM uses random 12-byte nonces per message

```json
// session_key message (Initiator → Responder)
{
  "type": "session_key",
  "ciphertext": "<ML-KEM-768 ciphertext hex, 1088 bytes>",
  "encryption_pk": "<initiator ML-KEM public key hex, 1184 bytes>"
}
```

Backward compatible: peers without `encryption_pk` or v1 peers communicate in plaintext.

### Connection Deduplication (v0.4.0+)

After authentication, duplicate connections to the same remote address are detected. Deterministic tie-breaker: the node with the lexicographically smaller address keeps its outbound connection; the other side closes its connection.

### Connection Flow

1. **Authenticated (v2)**: `connect()` → send `hello_auth` with challenge → await `auth_response` → verify responder sig → send `auth_confirm` → `peer.authenticated = True`
2. **Unauthenticated (v1 fallback)**: `connect()` → send `hello` → `peer.authenticated = False`
3. Failed auth: disconnect, no v1 fallback
4. Inbound without HELLO/HELLO_AUTH within 10s: disconnected

### Peer Validation

Rejected addresses:
- Port <= 0 or > 65535
- Blocked ports: 22, 23, 25, 53, 80, 443, 445, 3306, 5432, 6379, 8080, 8443
- Self-connection (same host:port)
- Link-local, reserved addresses (always)
- Private/loopback addresses (unless `ALLOW_PRIVATE_PEERS=true`)
- Cloud metadata hostnames

### Chain Sync

1. Periodic broadcast of `status` with current height
2. If any peer is ahead, send `get_blocks` to the best peer (not broadcast)
3. Process received blocks sequentially; stop on first invalid
4. Lock genesis hash after first block accepted

## 5. WebSocket Subscription Protocol

WebSocket endpoint: `WS /ws` on the RPC server port.

### Client → Server Messages

```json
// Subscribe to a channel
{"action": "subscribe", "channel": "new_block"}

// Unsubscribe from a channel
{"action": "unsubscribe", "channel": "new_block"}

// Keepalive ping
{"action": "ping"}
```

Available channels: `new_block`, `new_tx`, `chain_stats`.

### Server → Client Messages

```json
// Subscription confirmed
{"subscribed": "new_block"}

// Event (new_block)
{"channel": "new_block", "data": { /* block dict */ }}

// Event (new_tx)
{"channel": "new_tx", "data": { /* tx dict */ }}

// Periodic stats broadcast (every 5s when subscribers exist)
{"channel": "chain_stats", "data": {"height": 42, "tx_count": 310, "pool_size": 5, "peers": 3}}

// Pong response
{"action": "pong"}

// Error
{"error": {"code": 400, "message": "unknown channel"}}
```

### Limits

| Parameter | Value |
|-----------|-------|
| Max connections | 100 |
| Max subscriptions per client | 10 |
| Message rate limit | 10 msg/s per client |
| Max inbound message | 8 KB |
| Heartbeat | 30s (aiohttp built-in; auto-close on timeout) |

No authentication required. Event payloads contain only public chain data.

## 6. REST API

Base path: `/api/v1/`. All responses use the envelope format:

```json
{"data": <result>, "error": null}          // success
{"data": null, "error": {"code": N, "message": "..."}}  // error
```

### Public Endpoints

| Method | Path | Query Params | Response |
|--------|------|--------------|----------|
| GET | `/info` | — | Node info |
| GET | `/health` | — | `{"status": "ok"}` |
| GET | `/blocks` | `page`, `limit` | Paginated block list |
| GET | `/blocks/latest` | — | Most recent block |
| GET | `/blocks/:index` | — | Block by index |
| GET | `/blocks/hash/:hash` | — | Block by hash |
| GET | `/txs/:txid` | — | Transaction by ID |
| GET | `/txs/sender/:addr` | `page`, `limit` | Transactions by sender |
| GET | `/address/:addr` | — | Address summary |
| GET | `/notarizations/:hash` | — | All notarizations for document hash |
| GET | `/validators` | — | Active validator list |
| GET | `/pool` | `page`, `limit` | Pending transactions |
| GET | `/pool/count` | — | Pending transaction count |
| POST | `/verify` | — | Verify document hash against chain |

### Protected Endpoints (Bearer token required)

| Method | Path | Body |
|--------|------|------|
| POST | `/txs` | Raw transaction dict |
| POST | `/wallets` | `{password}` |
| GET | `/wallets` | — |
| POST | `/notarize` | `{wallet, document_hash, metadata?}` |
| POST | `/store` | `{wallet, document_hash, cid}` |
| POST | `/share` | `{wallet, recipient, cid, encapsulated_key}` |
| POST | `/register-validator` | `{wallet, validator_pubkey}` |
| POST | `/stake` | `{wallet, validator_address, amount}` |
| POST | `/delegate` | `{wallet, validator_address, amount}` |
| POST | `/unstake` | `{wallet, validator_address, amount}` |
| POST | `/evidence` | `{wallet, validator_address, block_index, ...}` |
| GET | `/stakes` | All validator stakes |
| GET | `/stakes/:validator` | Specific validator stake info |
| GET | `/epochs/current` | Current epoch info |
| GET | `/slashing-events` | Slashing event history |

Pagination: `page` is 1-based, `limit` default 20, max 100. Rate limiting, CORS, and bearer auth reuse the same implementation as the JSON-RPC server.

## 7. Wallet Encryption

### Key Derivation

```
salt = 32 random bytes
dk = scrypt(password, salt, N=16384, r=8, p=1, dklen=32)
```

Params enforced: `N >= 16384 and <= 2^20`, `r >= 8 and <= 16`, `p >= 1 and <= 4`.

### Encryption

```
plaintext = len(signing_sk) || signing_sk || len(encryption_sk) || encryption_sk
ciphertext = AES-256-GCM(dk, plaintext, aad=address)
```

Length-prefixed with 4-byte big-endian sizes. Exact sizes validated on decrypt (4032 + 2400 bytes).

### Integrity

- AES-GCM authentication tag detects wrong password or tampered file
- Address re-derived from decrypted signing_pk and compared
- No trailing data allowed after key material
