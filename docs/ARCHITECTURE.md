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
│              (QBit Network Client / CLI)                   │
├─────────────────────────────────────────────────────┤
│                   JSON-RPC API                       │
│             (node.py + rpc.py)                       │
│      Bearer auth | Body limits | Batch caps          │
├─────────────┬───────────────────┬───────────────────┤
│  Blockchain │    Consensus      │     P2P Network   │
│  (chain +   │    (PoA round-    │   (TCP + JSON     │
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

## Persistence

- `chain.json`: Atomic write via tempfile + os.replace
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
