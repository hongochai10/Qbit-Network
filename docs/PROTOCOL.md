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
  "type": "NOTARIZE | STORE | SHARE | REGISTER_KEY",
  "from": "qv1...",
  "to": "qv1... (recipient, required for SHARE)",
  "timestamp": 1700000000,
  "nonce": 0,
  "chainId": "qvault-mainnet",
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

No extra keys allowed (enforced by `_ALLOWED_KEYS` whitelist).

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
   - `validator == sorted_validators[index % n]` (round-robin)
   - Block signature valid against validator's ML-DSA public key
   - At least 1 transaction (no empty blocks)
   - Max 200 transactions
   - No duplicate tx_ids within block
   - No tx_ids already in chain (cross-block replay prevention)
   - All tx signatures valid
   - All tx payloads valid
   - Nonces sequential per sender within block
   - First nonce per sender matches chain state

## 4. P2P Protocol

### Transport

TCP with newline-delimited JSON messages. Reader limit: 10 MB.

### Message Types

| Type | Direction | Description |
|------|-----------|-------------|
| `hello` | Bidirectional | Handshake with node_id and port |
| `new_block` | Broadcast | New block announcement |
| `new_tx` | Broadcast | New transaction announcement |
| `get_blocks` | Request | Request blocks from index |
| `blocks` | Response | Block data response |
| `get_peers` | Request | Request peer list |
| `peers` | Response | Peer address list |
| `status` | Broadcast | Chain height announcement |

### Connection Flow

1. Outbound: `connect()` -> send `hello` -> start `_read_loop`
2. Inbound: `_on_connect()` -> wait for `hello` (10s timeout) -> migrate to real address -> read loop
3. Inbound without HELLO within 10s: disconnected (prevents idle socket DoS)

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

## 5. Wallet Encryption

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
