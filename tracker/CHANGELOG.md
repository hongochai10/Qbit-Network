# Changelog

## v0.3.0-sprint1 (2026-03-25)

### Protocol
- HELLO_AUTH mutual authentication: full server-side handler completing the 3-step ML-DSA-65 challenge-response flow (closes ISS-002)
  - Inbound `_handle_hello_auth_inbound`: validate fields, sign peer challenge, issue counter-challenge
  - `_handle_auth_response`: verify responder signature, send `auth_confirm`, mark peer authenticated
  - `_handle_auth_confirm`: verify initiator signature over counter-challenge, mark peer authenticated
  - Auth gating: `new_block`, `new_tx`, `get_blocks`, `blocks` rejected from unauthenticated v2 peers after grace period
  - Domain-separated signatures (`QBIT_AUTH_v2:` + challenge + signer address)
  - Single-use challenges (`os.urandom(32)`); monotonic deadline tracking (`time.monotonic()`)

### Validator Registry
- `REGISTER_VALIDATOR` transaction type for on-chain validator key distribution (closes ISS-008)
  - Payload: `validator_pubkey` (ML-DSA-65, 1952 bytes hex) + `validator_address` (derived, verified)
  - Reject duplicate registration: address already in registry returns error
  - Genesis validator auto-registered in memory on `init_chain()`
  - SQLite `validator_registry` table; full rollback support during reorg
  - RPC `qv_registerValidator` method for validator self-registration

### Security
- Token bucket rate limiting (closes Planned item):
  - P2P: per-peer IP, 20 msg/s sustained, 100 burst; disconnect after 3 violations; HELLO/HELLO_AUTH exempt
  - RPC: per-client IP, 10 req/s sustained, 50 burst; HTTP 429 with JSON-RPC error; GET / exempt
  - LRU cap at 10k tracked IPs; active-peer eviction exclusion; periodic stale cleanup every 60s
- 8 Round 13 audit findings fixed: auth grace period bypass (SPRINT1-001), v1 downgrade on failed auth (SPRINT1-002), auth handshake rate limiting (SPRINT1-004), validator registry cross-reference (SPRINT1-005), REGISTER_VALIDATOR overwrite (SPRINT1-006), rate limiter LRU eviction bypass (SPRINT1-008), monotonic auth deadline (SPRINT1-010), non-atomic validator SQLite during reorg (SPRINT1-012)
- 3 findings deferred to v0.4.0: responder-signs-before-verify (SPRINT1-003), genesis validator on-chain tx (SPRINT1-007), SQLite string concat in validator table (SPRINT1-011)

### Tests
- 75 new tests: 46 adversarial + 29 integration — 222 total (up from 147)
- CI pipeline split into 3 parallel jobs: unit, adversarial, integration; all jobs run Python 3.11 + 3.12 matrix (closes ISS-015)

## v0.2.1 (2026-03-25)

### CI Expansion (ISS-015)
- Adversarial test suite (`tests/test_adversarial.py`): 46 tests covering double-spend, replay, invalid signatures, future timestamps, nonce manipulation, oversized payloads, tampered block hashes, fork attacks, empty blocks, invalid validators, tx type mismatches, SSRF, and wallet tampering
- Integration test suite (`tests/test_integration.py`): 29 tests covering full lifecycle (wallet to proof), multi-wallet sharing, chain persistence/reload (SQLite + JSON migration), fork resolution convergence, concurrent submissions, proof export/verify, query APIs, and edge cases
- CI pipeline (`.github/workflows/ci.yml`) split into 3 parallel jobs: unit tests, adversarial tests, integration tests, plus a test summary job
- All jobs run across Python 3.11 and 3.12 matrix with JUnit XML reporting and artifact uploads
- Total test count: 222 tests (up from 147)

### Validator Registry (ISS-008)
- New `REGISTER_VALIDATOR` transaction type for on-chain validator key distribution
- Payload: `validator_pubkey` (ML-DSA-65, 1952 bytes hex) + `validator_address` (derived, verified)
- `_validator_registry` in Blockchain maps address to signing pubkey
- Genesis validator auto-registered during `init_chain()`
- Validators registered via on-chain tx are added to consensus for block production/validation
- SQLite `validator_registry` table for persistent storage across restarts
- Full rollback support: validator registrations reverted during reorg
- RPC `qv_registerValidator` method for validator self-registration
- RPC `qv_validators` and `qv_nodeInfo` updated to include on-chain registered validators
- Backward compatible: existing chains load without REGISTER_VALIDATOR txs

### Security
- Token bucket rate limiting for P2P (per-peer IP, 20 msg/s sustained, 100 burst) and RPC (per-client IP, 10 req/s sustained, 50 burst)
- P2P: peers disconnected after 3 rate-limit violations; HELLO/HELLO_AUTH exempt
- RPC: returns HTTP 429 with JSON-RPC error; GET / (health) exempt
- Localhost (127.0.0.1/::1) exempt from rate limiting in development
- LRU eviction at 10k tracked IPs; periodic stale-entry cleanup every 60s

### Protocol
- HELLO_AUTH full server-side handler: 3-step ML-DSA-65 challenge-response P2P authentication (closes ISS-002)
  - Outbound `connect()` sends `hello_auth` with 32-byte random challenge when signing keys are available
  - Inbound `_on_connect` handles `hello_auth` or `hello` (v1 fallback) as first message
  - `_handle_hello_auth_inbound`: validates fields, signs peer challenge, generates counter-challenge, sends `auth_response`
  - `_handle_auth_response`: verifies responder signature, signs counter-challenge, sends `auth_confirm`, marks `peer.authenticated = True`
  - `_handle_auth_confirm`: verifies initiator signature over counter-challenge, marks `peer.authenticated = True`
- Auth gating: `new_block`, `new_tx`, `get_blocks`, `blocks` messages rejected from unauthenticated v2 peers (after auth grace period)
- Peer fields: `challenge` (pending 32-byte nonce), `remote_pubkey` (authenticated ML-DSA pubkey), `auth_deadline` (10s timeout)
- Challenges are single-use (cleared before verification) and cryptographically random (`os.urandom`)
- Domain-separated signatures (`QBIT_AUTH_v2:` + challenge + signer address) prevent cross-protocol reuse
- Timestamp validated within MAX_BLOCK_DRIFT (30s); chain_id checked against CHAIN_ID
- v1 backwards compatibility: peers without signing keys use plain `hello`, skip auth requirement
- Protocol versioning: PROTOCOL_VERSION=2, negotiated via min(initiator, responder)
- Drop authority scoring — pure longest-chain fork resolution (first-seen wins on tie)

### Client
- HTML proof certificate export: `qbit proof <file> --format html`
- Auto key registration: `qbit wallet create --register --token TOKEN`
- CLI store command: `qbit store <file>` — record document hash on-chain
- CLI share command: `qbit share <file> --to <addr>` — ML-KEM encrypted sharing
- Full CLI: 7 commands (wallet, notarize, verify, proof, store, share, verify-proof)

### Infrastructure
- Dockerfile: multi-stage build (python:3.11-slim + liboqs 0.12.0)
- docker-compose.yml: 3-validator testnet with bridge network
- Exposed RPC ports 8545-8547 for external access

### Tests
- 166 tests passing across 7 test files
- 12 audit rounds, 116+ issues found and fixed

## v0.2.0 (2026-03-25)

### Protocol
- Fork resolution: longest valid chain rule replaces permanent divergence on conflicting blocks (closes ISS-003)
- Request-ID correlation: unsolicited MSG_BLOCKS are now rejected, preventing chain-split between honest nodes (closes ISS-005)

### Storage
- LevelDB/SQLite persistent backend replaces the in-memory chain; blocks and indices survive node restarts (closes ISS-006)

### Security
- TLS support for the RPC server via reverse-proxy mode; shared secrets no longer exposed over plain HTTP (closes ISS-004)

### Performance
- Consensus nonce validation reduced from O(n^2) to O(n) using a precomputed sender-count map (closes ISS-011)

### Client
- CLI tool added: wallet creation, key listing, and NOTARIZE submission from the command line
- Merkle proof export: `getProof` RPC method and CLI flag produce a portable JSON proof bundle

### Infrastructure
- CI/CD pipeline added: unit test suite runs on every push

### Known Issues Introduced
- ISS-015: CI pipeline covers unit tests only; adversarial and integration tests not yet included
- ISS-016: TLS termination is external (reverse proxy); in-process TLS deferred to v0.3.0
- ISS-017: CLI does not yet expose STORE or SHARE workflows

---

## v0.1.0 (2026-03-25)

### Initial Release

**Crypto Layer**
- ML-DSA-65 (CRYSTALS-Dilithium) signatures via liboqs
- ML-KEM-768 (CRYSTALS-Kyber) key encapsulation via liboqs
- SHA3-256 / SHAKE-256 hashing (stdlib hashlib)
- AES-256-GCM authenticated encryption (cryptography library)
- Domain-separated Merkle tree (prevents second-preimage attacks)

**Core**
- Dual-keypair wallet (ML-DSA signing + ML-KEM encryption)
- Wallet encryption: scrypt KDF (N=16384, r=8, p=1) + AES-256-GCM
- 4 transaction types: NOTARIZE, STORE, SHARE, REGISTER_KEY
- Block structure with ML-DSA signed headers and Merkle roots
- Proof of Authority consensus with round-robin validator selection
- Per-sender nonce ordering with cross-block replay prevention
- On-chain encryption key registry with version history
- First-notarization preservation (subsequent don't overwrite)
- Monotonic block timestamps: max(time(), parent+1)
- Self-produced blocks validated through consensus before commit

**Networking**
- TCP P2P with newline-delimited JSON protocol
- Peer discovery via gossip (MSG_PEERS)
- Chain sync with height exchange (MSG_STATUS)
- SSRF protection (private IPs, metadata endpoints, blocked ports)
- MAX_PEERS=50 enforced on inbound + outbound
- 10-second HELLO timeout on inbound connections

**RPC**
- JSON-RPC 2.0 with batch support (max 50)
- Bearer token authentication (constant-time comparison)
- 22 methods (11 public, 11 protected)
- Body size limit (1MB, enforced at aiohttp level)
- All parameters validated with isinstance checks
- Error messages sanitized (200 char max)

**Persistence**
- Atomic chain writes (tempfile + os.replace)
- Atomic wallet writes with 0o600 permissions
- Chain load validation (hash chain, tx sigs, block sigs)
- Wallet persistence across node restarts

**Security**
- 9 rounds of security audit (104 issues found and resolved)
- See tracker/AUDIT_LOG.md for complete audit trail
