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
| Transaction pool not persisted across restarts | Accepted | WAL-based pool persistence planned |
| No block finality checkpoint | Accepted | Checkpoint mechanism planned |
| Sybil/Eclipse attacks (residual) | Accepted | HELLO_AUTH + reputation scoring raise the bar; full Sybil resistance needs staking bonding |

### Resolved in v0.4.0

| Risk | Resolution |
|------|-----------|
| Responder signs before verifying initiator fields (SPRINT1-003) | Initiator proof in hello_auth; responder verify-before-sign (v0.4.0-sprint2) |
| Genesis validator not on-chain (SPRINT1-007) | Genesis validator registered via REGISTER_VALIDATOR tx in genesis block (v0.4.0-sprint1) |
| SQLite string concat in validator table (SPRINT1-011) | Parameterized queries (v0.4.0-sprint1) |
| No peer reputation / Sybil mitigation (ISS-009) | dPoS slashing + PeerReputation scoring + P2P encrypted channel (v0.4.0) |
| P2P not encrypted | ML-KEM-768 session key + AES-256-GCM encrypted channel (v0.4.0-sprint2) |
| Python `bytes` key material persists in heap (ISS-001) | SecureBytes ctypes buffer with explicit zero(); wallet.close() zeros all keys (v0.4.0-sprint3) |
| TLS self-signed cert UX (ISS-016) | TLSManager auto-generates, renews, and hot-reloads certificates (v0.4.0-sprint3) |
| No chain pruning (ISS-007) | Blockchain.prune() removes old SQLite rows while preserving all indices (v0.4.0-sprint3) |

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

31 rounds of security audit, 290+ issues found:

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
| 14 | v0.4.0 Sprint 1-2 (dPoS, epochs, slashing, P2P encryption, dedup) | 9 |
| 15 | v0.4.0 Sprint 3 (SecureBytes, TLS auto-provisioning, reputation, pruning) | 5 |
| 16 | v0.5.0 Sprint 4 Financial layer security (TRANSFER, fees, rewards, supply) | 5 |
| 17 | v0.6.0 EIP-1559 + auth bypass (CRITICAL auth bypass, unbonding persistence) | 2 |
| 18 | v0.7.0 State proofs, receipts, webhooks, SDK (SSRF, injection, memory) | 5 |
| 19 | Combined 5-agent audit (state proofs, receipts, persistence, perf) | 13 |
| 20 | Pre-phase-2 audit (webhook DNS rebinding, slashing pruning) | 4 |
| 21 | v0.8.0 release audit (token rollback, pool admission, P2P codec) | 11 |
| 22 | v0.8.0 final verification (docs consistency) | 2 |
| 23 | PQC deep-dive + issue hunt (partial block application, race conditions) | 7 |
| 24 | Financial layer security (CRITICAL epoch reward supply inflation) | 1 |
| 25 | CEO full audit (WebSocket auth, REST auth, PoA clock, CORS) | 9 |
| 26 | CEO full audit (webhook auth, dashboard scope, TLS writes, token queries) | 9 |
| 27 | CEO comprehensive audit (SSRF DNS, WebSocket heartbeat, block events) | 4 |
| 28 | CEO full audit (fee types, state trie injection, wallet locks, token indices) | 8 |
| 29 | CEO comprehensive audit (inbound P2P IP bypass, token holder DoS) | 5 |
| 30 | CEO comprehensive audit (amount bounds, fee bypass, state proof enumeration) | 9 |
| 31 | CEO comprehensive audit (REST param injection, pagination, tracker reconciliation) | 7 |

See `tracker/AUDIT_LOG.md` for the complete log with all findings per round.

### REST API & Tracker Reconciliation (Round 31)

- **[MEDIUM] REST evidence param injection**: `_submit_evidence` passes raw request body as `**kwargs` — allows injection of unexpected parameters.
- **[MEDIUM] REST pagination validation**: `_get_token_holders`/`_get_address_tokens` use bare `int()` cast without validation — 500 error on bad input with path leak.
- **[LOW] Block baseFee validation**: `from_dict` does not validate `baseFee` type/range — peer can crash handler with bad type.
- **[LOW] Token list ordering**: ~~`list_tokens` returns dict insertion order~~ — **fixed**: sorted by `token_id` before pagination (R31-004).
- **[LOW] Webhook event materialization**: `_deliver_block_webhooks` materializes all events before filtering — unnecessary memory allocation.
- **[INFO] Dynamic fee activation**: `DYNAMIC_FEE_ACTIVATION_HEIGHT` default 2^63 effectively disables dynamic fees — needs env var activation.
- **Tracker reconciliation**: 5 issues (R26-005, R29-002, R30-002, R30-005, R30-007) confirmed fixed in code and marked done.

### Amount Bounds & State Proof Security (Round 30)

- **[MEDIUM] Unbounded amounts**: TRANSFER/STAKE/MINT amount not capped — pool DoS via big integers in `_pending_debits`.
- **[MEDIUM] P2P fee bypass**: P2P-received transactions bypass fee param upper bound (2^63 cap in node.py only). Fixed in `Transaction.from_dict()`.
- **[MEDIUM] State proof enumeration**: State proof endpoint accepts arbitrary trie key — unauthenticated token balance probing (extends R29-003).
- **[LOW] Auth attempts unbounded**: `_auth_attempts` dict grows without limit under IP rotation. Fixed with LRU cap.
- **[LOW] State tree O(n) index**: `get_proof()` uses `list.index` instead of `bisect`. Fixed.

### Inbound P2P & Token Holder DoS (Round 29)

- **[MEDIUM] Token holder DoS**: `get_token_holders()` materializes full holder list before pagination — DoS via unauthenticated public endpoint.
- **[MEDIUM] Inbound P2P IP bypass**: Inbound P2P connections bypass `_is_safe_peer()` validation — private IPs accepted. Fixed with `_is_safe_inbound_ip()`.
- **[LOW] State proof arbitrary key**: `qv_getStateProofAt` accepts arbitrary trie key — unauthenticated token balance enumeration.
- **[LOW] P2P readline incompatibility**: Inbound P2P first message uses `readline()` — incompatible with binary wire format.

### State Proofs, Receipts, Webhooks, SDK Security (Round 18)

- **[HIGH] Webhook SSRF**: Webhook registration now blocks private/loopback/link-local/metadata IP addresses, preventing Server-Side Request Forgery via webhook URL targeting internal services.
- **[HIGH] SDK query parameter injection**: SDK client now URL-encodes all query parameters via `urllib.parse.urlencode()`, preventing injection of extra parameters or path traversal.
- **[MEDIUM] State snapshot memory growth**: State trie snapshots are now pruned beyond `MAX_REORG_DEPTH`, preventing unbounded memory growth on long-running nodes.
- **[MEDIUM] REST events endpoint limit bypass**: The `/events` endpoint now enforces the same `1-100` limit range as other paginated endpoints.
- **[LOW] Webhook delivery task list accumulation**: Delivery task cleanup uses `done_callback` with safe removal; bounded by MAX_WEBHOOKS * MAX_EVENTS_PER_HOOK * RETRY_DELAYS.

### EIP-1559 and Auth Security (Round 17)

- **[CRITICAL] Auth bypass**: Fixed a critical authentication bypass vulnerability. See `tracker/AUDIT_LOG.md` Round 17 for details.
- **Unbonding persistence**: Fixed unbonding state persistence to survive node restarts correctly.
- **EIP-1559 anti-spam**: `maxFeePerWeight` pool admission gate rejects under-priced TXs before they consume validator resources.
- **Self-TX weight cap**: Validator self-TXs capped at 25% of total block weight, preventing artificial base fee manipulation.

### Financial Layer Security (Round 16)

- **Supply conservation**: epoch reward distribution now debits validator balance (R16-003 fix prevents inflation)
- **Recipient validation**: TRANSFER recipient must be a valid `qv1` address format (67 chars, hex suffix) -- prevents funds sent to unrecoverable addresses
- **Double-spend prevention**: `_pending_debits()` sums all pending pool debits before allowing new transfers
- **Fee atomicity**: fees deducted sequentially during block processing with `_debit()` balance checks
- **Integer arithmetic only**: all balance operations use Python `int`, no floating-point
- **Rollback correctness**: epoch distribution rollback uses explicit credit/debit records for clean reversal

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

- HELLO_AUTH 4-step ML-DSA-65 mutual authentication: initiator embeds proof in hello_auth; responder verifies proof before signing (verify-before-sign); both sides verify the other's signature before marking `peer.authenticated = True`
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
