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
| Sybil/Eclipse attacks | Accepted | HELLO_AUTH raises bar; needs peer reputation |
| ~~Responder signs before verifying initiator fields (SPRINT1-003)~~ | **Resolved v0.4.0** | Initiator includes proof in hello_auth; responder verifies before signing |
| ~~Genesis validator not registered via on-chain tx (SPRINT1-007)~~ | **Resolved v0.4.0** | Genesis validator registered via REGISTER_VALIDATOR tx in genesis block |
| ~~SQLite validator table uses string concat, not parameterized (SPRINT1-011)~~ | **Resolved v0.4.0** | Parameterized queries implemented |

### Resolved in v0.4.0

| Risk | Resolution |
|------|-----------|
| Responder signs before verifying initiator fields (SPRINT1-003) | Initiator proof in hello_auth; responder verify-before-sign (v0.4.0-sprint2) |
| Genesis validator not on-chain (SPRINT1-007) | Genesis validator registered via REGISTER_VALIDATOR tx in genesis block (v0.4.0-sprint1) |
| SQLite string concat in validator table (SPRINT1-011) | Parameterized queries (v0.4.0-sprint1) |
| No peer reputation / Sybil mitigation (ISS-009) | Slashing for misbehavior + P2P encrypted channel (v0.4.0) |
| P2P not encrypted | ML-KEM-768 session key + AES-256-GCM encrypted channel (v0.4.0-sprint2) |

### Resolved in v0.2.0-v0.3.0

| Risk | Resolution |
|------|-----------|
| P2P not authenticated | HELLO_AUTH ML-DSA challenge-response (v0.2.1, completed v0.3.0-sprint1) |
| No fork resolution | Pure longest-chain with try_reorg (v0.2.0) |
| In-memory only | SQLite-primary persistence (v0.3.0-sprint2) |
| Secrets over HTTP | TLS support with --tls-cert/--tls-key (v0.2.0) |
| No key revocation | REVOKE_KEY transaction type (v0.3.0-sprint2) |
| No on-chain validator key distribution | REGISTER_VALIDATOR tx type + persistent registry (v0.3.0-sprint1) |
| No rate limiting | Token bucket per peer/client (v0.3.0-sprint1) |

## Audit History

14 rounds of security audit, 181+ issues found:

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
| 10 | v0.2.0 feature audit (fork, TLS, CLI, store) | 7 |
| 11 | Rate limiting + auth baseline | 9 |
| 12 | v0.2.1 full audit (P2P auth, Docker, store/share) | 9 |
| 13 Sprint 1 | v0.3.0 Sprint 1 (HELLO_AUTH, REGISTER_VALIDATOR, rate limiting, CI) | 14 |
| 13 Sprint 2 | v0.3.0 Sprint 2 (SQLite-primary, REVOKE_KEY, REST API, WebSocket) | 16 |

Round 13 Sprint 2 findings (SPRINT2-001 through SPRINT2-016): see `tracker/AUDIT_LOG.md` for the complete log.

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

### Authentication Protocol

- HELLO_AUTH 3-step ML-DSA-65 challenge-response: both sides verify the other's signature before marking `peer.authenticated = True`
- Failed auth triggers immediate disconnect; no downgrade to unauthenticated state (no v1 fallback on failure)
- Challenges are 32-byte single-use random values (`os.urandom(32)`), cleared before verification
- Signature domain: `"QBIT_AUTH_v2:" || challenge || signer_address` prevents cross-protocol reuse
- Auth deadline tracked with `time.monotonic()` to prevent wall-clock skew bypass (SPRINT1-010 fix)
- Auth gating enforced on every message after grace period, not just at expiry (SPRINT1-001 fix)
- HELLO/HELLO_AUTH messages themselves counted in rate limiter to prevent handshake flood (SPRINT1-004 fix)

### Rate Limiting

- Token bucket per peer IP (P2P) and per client IP (RPC)
- P2P: 20 msg/s sustained, 100 burst; disconnect after 3 violations
- RPC: 10 req/s sustained, 50 burst; HTTP 429 on violation
- LRU cap at 10,000 tracked IPs; active peers excluded from eviction (SPRINT1-008 fix)
- `asyncio.Lock` per bucket prevents shared-state race under concurrent requests

### Key Revocation Security Model

- Self-revocation only: the `from` address must own the key being revoked
- Signing key revocation: address immediately blocked from submitting transactions at both `submit_tx` and consensus validation layers
- Validator revocation: validator removed from active set; existing blocks remain valid (revocation is not retroactive)
- The genesis validator's signing key is permanently protected from revocation
- Idempotency guard: revoking an already-revoked key is rejected to prevent log pollution
- Revocations are rolled back atomically during chain reorg, restoring prior state

### dPoS Security Model

- Stake-weighted validator selection prevents low-stake validators from dominating block production
- Slashing for double-signing: 50% stake reduction enforced by EVIDENCE transactions
- Slashed validators cannot receive new stake (`_slashed_validators` set)
- Unbonding period (100 blocks) prevents immediate stake withdrawal after misbehavior
- Epoch rotation: validator set frozen per epoch prevents mid-epoch manipulation
- Evidence processing validates both ML-DSA signatures against validator pubkey
- Duplicate evidence rejected to prevent repeated slashing of the same validator

### P2P Encrypted Channel

- ML-KEM-768 key exchange after mutual authentication establishes session keys
- AES-256-GCM encryption for all post-authentication P2P messages
- Session key derived via SHA3-256 from ML-KEM shared secret (32 bytes)
- Random 12-byte nonces per message prevent nonce reuse
- Backward compatible: v1 peers and peers without encryption keys use plaintext
- Connection deduplication prevents resource exhaustion from redundant connections

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
