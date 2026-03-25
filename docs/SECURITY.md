# QBit Network Security Model

## Threat Model

### What QBit Network protects against

- **Quantum computer attacks** on digital signatures and key exchange
- **Document timestamp forgery** (notarization is immutable on-chain)
- **Unauthorized data access** (ML-KEM encapsulation, AES-256-GCM encryption)
- **Chain tampering** (ML-DSA signed blocks, Merkle proofs, hash chain integrity)
- **RPC abuse** (bearer token auth, body/batch limits, input validation)
- **Network attacks** (SSRF prevention, peer limits, message size caps, idle socket timeout)

### What QBit Network does NOT protect against (accepted risks)

| Risk | Status | Mitigation Path |
|------|--------|-----------------|
| Python `bytes` key material in heap | Accepted | Requires C extension / mmap for zeroing |
| P2P messages not authenticated | Accepted | Needs Noise Protocol or mutual TLS |
| No fork resolution | Accepted | PoA single-validator sufficient for MVP |
| In-memory chain | Accepted | Needs LevelDB/RocksDB for production scale |
| Shared secrets over HTTP | Accepted | Deploy behind TLS reverse proxy |
| Sybil/Eclipse attacks | Accepted | Needs peer reputation system |

## Audit History

9 rounds of security audit, 104 issues found and resolved:

| Round | Focus | Issues |
|-------|-------|--------|
| 1 | Basic correctness | 14 |
| 2 | Deep crypto + protocol | 21 |
| 3 | Line-by-line review | 16 |
| 4 | Regression analysis | 3 |
| 5 | Automated security agent | 21 |
| 6 | Red team adversarial | 9 |
| 7 | Fix regression + edge cases | 11 |
| 8 | Module consistency | 4 |
| 9 | Semantic + protocol correctness | 5 |

## Key Security Controls

### Cryptographic

- ML-DSA-65 signatures with exception handling on sign/verify
- ML-KEM-768 with strict input size validation (pk=1184, sk=2400, ct=1088 bytes)
- AES-256-GCM with 12-byte random nonce, minimum ciphertext length check
- scrypt KDF with enforced min/max params (N=16384..2^20, r=8..16, p=1..4)
- Domain-separated Merkle tree (`\x00` leaf prefix, `\x01` node prefix)
- Constant-time token comparison (`hmac.compare_digest`)

### Chain Integrity

- Self-produced blocks validated through full consensus before appending
- Monotonic timestamps: `max(time.time(), parent.timestamp + 1)`
- Cross-block transaction replay prevention (`_chain_tx_ids` set)
- Per-sender nonce ordering validated in consensus (both intra-block and vs chain state)
- First notarization preserved (subsequent notarizations of same hash don't overwrite)
- Encryption key history maintained (all REGISTER_KEY versions kept)
- Atomic chain load (all-or-nothing validation into temp list)

### Network

- SSRF prevention: block private/reserved IPs, link-local, cloud metadata
- MAX_PEERS enforced on both inbound and outbound connections
- 10-second HELLO timeout on inbound connections
- P2P re-broadcasts canonical form (not raw peer data)
- Sync requests sent to best peer only (no broadcast amplification)
- Inbound blocks/peers list capped (100 blocks, 50 peers per message)

### RPC

- Bearer token authentication for all write/sensitive operations
- `client_max_size` enforced at aiohttp level (handles chunked encoding)
- Batch request limit: 50
- Error messages truncated to 200 chars
- Auth token masked in logs
- Info endpoint only exposes public method names
- All params validated with `isinstance` type checks

### Persistence

- Atomic writes: tempfile + os.replace for both chain and wallet files
- Wallet files: permission 0o600, chmod before replace (no TOCTOU window)
- Chain load validates: structure, tx signatures, block signatures (when validator known)
- Empty chain.json `[]` treated as fresh (prevents validator stall)
- Double-load guard (skip if chain already loaded)
