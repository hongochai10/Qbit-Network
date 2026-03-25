# Feature Tracker

## Implemented

### v0.4.0-sprint2 (2026-03-25)
- [x] Epoch Rotation
  - Every EPOCH_LENGTH blocks (100), active validators are frozen for that epoch
  - `_current_epoch`, `_epoch_validators`, `_epochs` state tracked in Blockchain
  - Stake changes during an epoch take effect at the next epoch boundary
  - `get_current_epoch()`, `get_epoch_validators()` query methods
  - SQLite `epochs` table (epoch_number, block_start, validators_json)
  - Epoch rollback when rolling back past epoch boundaries
  - RPC: `qv_getEpoch` (public), REST: `GET /epochs/current`
- [x] Slashing (Double-Sign Evidence)
  - EVIDENCE TxType for reporting double-sign misbehaviour
  - `Transaction.evidence()` factory method with full payload validation
  - Signature verification: both block signatures checked against validator pubkey
  - SLASH_PERCENTAGE=50 config: slashes all stakers proportionally
  - Validator removed from active set if stake drops below MIN_STAKE
  - `_slashed_validators` set prevents re-staking to slashed validators
  - `_processed_evidence` set prevents duplicate slashing
  - SQLite `slashing_events` table (validator, evidence_tx_id, amount_slashed, block_index)
  - Slashing rollback supported
  - RPC: `qv_submitEvidence` (protected), `qv_getSlashingEvents` (public)
  - REST: `POST /evidence`, `GET /slashing-events`
  - EVIDENCE payloads use 32KB limit (contains two ML-DSA-65 signatures)
  - 37 new tests (9 tx validation, 6 epoch, 9 slashing, 2 rollback, 6 SQLite, 5 edge cases)
- [x] Auth Verify-Before-Sign fix (SPRINT1-003)
  - Initiator signs proof = Sign(sk, domain || challenge || address) in hello_auth
  - Responder verifies proof BEFORE signing anything (prevents identity confusion)
  - Missing, empty, invalid, or wrong-key proof rejected
- [x] P2P Encrypted Channel
  - ML-KEM-768 key exchange after mutual authentication
  - AES-256-GCM encrypted transport for all post-auth messages
  - Session key derived via SHA3-256 from ML-KEM shared secret
  - Initiator encapsulates shared secret using responder's encryption_pk
  - Encrypted message format: `{"type": "encrypted", "data": ciphertext_hex}`
  - Graceful fallback: v1 peers and peers without encryption_pk stay plaintext
  - Peer fields: session_key, encrypted, encryption_pk
  - broadcast() uses send_encrypted() for automatic encryption when available
- [x] Connection Deduplication (A-01)
  - Detects duplicate connections to same remote address after authentication
  - Deterministic tie-breaker: node with smaller address keeps its outbound connection
  - Peer fields: is_initiator, remote_address
  - 48 new tests (7 proof, 22 encryption, 10 dedup, 1 full handshake+encryption, 8 misc)

### v0.4.0-sprint1 (2026-03-25)
- [x] Genesis validator on-chain transaction (SPRINT1-007)
  - Genesis validator registered via REGISTER_VALIDATOR tx in genesis block
  - Replaces direct \_validator_registry write in init_chain()
  - Block.genesis() accepts optional transactions parameter
  - Genesis block txs do not consume user-facing nonce slots
  - Rollback/reload work uniformly through \_append_block code path
  - Auto-stake MIN_STAKE preserved for production use (explicit validator_pk)
  - Pre-existing dPoS \_select_dpos sha3_256().digest() bug fixed
- [x] Delegated Proof of Stake (dPoS) consensus
  - 3 new transaction types: STAKE, DELEGATE, UNSTAKE
  - Stake-weighted deterministic validator selection (SHA3-256 seed from parent_hash + block_index)
  - Backward-compatible PoA round-robin fallback when no validators are staked
  - Unbonding period (100 blocks) for unstaking operations
  - In-memory staking state: `_stakes`, `_total_stake`, `_unbonding`
  - SQLite persistence: `stakes` and `unbonding` tables with full rollback support
  - Staking validation: registered validator check, sufficient stake check for unstake
  - Rollback support for STAKE/DELEGATE/UNSTAKE in `_rollback_block`
  - JSON-RPC: `qv_stake`, `qv_delegate`, `qv_unstake` (protected), `qv_getStake`, `qv_getValidatorStakes` (public)
  - REST API: `GET /stakes`, `GET /stakes/:validator`, `POST /stake`, `POST /delegate`, `POST /unstake`
  - Config constants: MIN_STAKE=1, MAX_STAKE=1,000,000, UNBONDING_PERIOD=100, EPOCH_LENGTH=100
  - 58 new tests in `tests/test_dpos.py`

### v0.3.0-sprint3 (2026-03-25)
- [x] IPFS integration for CLI store/share/retrieve commands
  - `cli/ipfs_client.py` — lightweight IPFS HTTP API client (stdlib-only, no pip deps)
  - Methods: `add_file()`, `add_bytes()`, `cat()`, `pin_ls()`, `is_available()`
  - CID format validation (CIDv0 `Qm...` and CIDv1 `bafy...`)
  - Configurable file size limit (default 10MB), 30s upload / 10s read timeouts
  - `qbit store --ipfs` — hash file, pin to IPFS, record CID + hash on-chain
  - `qbit share --ipfs` — pin file to IPFS, submit SHARE tx with CID
  - `qbit retrieve <cid>` — fetch from IPFS, optional `--output`, `--verify-hash`
  - `--ipfs-api` flag on store/share/retrieve (default `http://127.0.0.1:5001`)
  - Graceful fallback: IPFS unavailable warns and uses `local:` reference
  - Manual `--cid` still supported (takes precedence over `--ipfs`)
  - 35 tests (client unit tests + CLI integration tests with mocked IPFS/RPC)
- [x] Web dashboard / chain explorer — single-file SPA at `/dashboard/`
  - Real-time block feed via WebSocket (`new_block`, `new_tx`, `chain_stats` channels)
  - Recent Blocks table with click-to-expand detail view and transaction listing
  - Transaction Viewer — search by TX ID with type-specific payload display
  - Validator Panel — list of registered validators with status indicators
  - Document Verifier — SHA3-256 hash verification via REST `/verify` endpoint
  - Pool Monitor — pending transaction count + breakdown by type with visual bar
  - Live Stats Bar: chain height, total txs, pending pool, validators, avg block time
  - Configurable API endpoint + auth token, settings persisted in localStorage
  - Auto-reconnecting WebSocket with exponential backoff (1s-30s)
  - Dark theme, responsive layout, XSS-safe DOM escaping, copy-on-click hashes
  - No external dependencies — pure HTML/CSS/vanilla JS, < 35KB total
  - Static file serving route added to RPCServer at `/dashboard/`

### v0.3.0-sprint2 (2026-03-25)
- [x] SQLite-primary storage: removed in-memory chain list for disk-backed blockchains
  - Blocks stored only in SQLite when `data_dir` is set (no more dual-write memory overhead)
  - Cached `_latest_block` and `_height` for O(1) tip access
  - `_ChainProxy` backward-compatible list-like interface for `bc.chain` access
  - In-memory mode (no `data_dir`) retains list for tests/ephemeral use
  - `get_blocks_range()` and `get_blocks_count()` added to SQLiteStore
  - `get_next_nonce()` alias added (ISS-012 clarification)
  - Rollback refactored: pre-fetch blocks before SQLite deletion in `_rollback_to()`
  - `node.py` updated to use `get_block()`/`height` instead of `self.blockchain.chain`
- [x] `REVOKE_KEY` transaction type for on-chain key revocation (ISS-010)
- [x] Three key types: `signing`, `encryption`, `validator`; three reasons: `compromised`, `rotation`, `decommission`
- [x] Revocation registry (`_revoked_keys`) with `is_key_revoked()` and `get_revocation_info()` queries
- [x] Revoked signing keys blocked from submitting further transactions (submit_tx + consensus validation)
- [x] Validator revocation removes from `_validator_registry` + `consensus.validators`
- [x] Genesis validator cannot be revoked (safety check)
- [x] Idempotency: cannot revoke an already-revoked key
- [x] Full rollback support: revocations reverted during reorg, validators re-added
- [x] SQLite `revoked_keys` table with `put_revocation()`, `get_revocation()`, `delete_revocation()`, `get_all_revocations()`
- [x] RPC `qv_revokeKey` method for self-revocation
- [x] 28 revocation tests (payload validation, signing/encryption/validator revocation, rollback, persistence, adversarial)
- [x] REST API gateway — 26 endpoints (13 public GET, 8 protected POST, 5 protected GET/POST) at `/api/v1/`
- [x] Pagination support (page/limit) on blocks and transaction listing endpoints
- [x] CORS middleware with configurable origins, preflight OPTIONS handling
- [x] Consistent JSON response structure: `{"data": ..., "error": null}` / `{"data": null, "error": {"code": N, "message": "..."}}`
- [x] Bearer auth reuse from RPC server (constant-time comparison)
- [x] Input validation on all parameters with proper HTTP status codes (400/401/404/429/500)
- [x] REST sub-app mounted on existing aiohttp server alongside JSON-RPC
- [x] 47 REST API tests (public endpoints, protected endpoints, CORS, response structure)
- [x] WebSocket subscriptions at `/ws` — real-time `new_block`, `new_tx`, `chain_stats` events
- [x] WebSocketManager: channel-based pub/sub, max 100 connections, 10 subs/client, 10 msg/s rate limit
- [x] Periodic `chain_stats` broadcast every 5s (height, tx_count, pool_size, peers) — only when subscribers exist
- [x] Event emission on block production, block receipt from P2P, tx submission (RPC + P2P)
- [x] JSON subscription protocol: subscribe/unsubscribe/ping/pong with error handling
- [x] aiohttp built-in heartbeat (30s ping, auto-close on timeout), graceful disconnect cleanup
- [x] 34 WebSocket tests (18 unit, 16 integration via TestServer)

### v0.3.0-sprint1 (2026-03-25)
- [x] HELLO_AUTH full server-side handler + challenge verification (ISS-002) — 3-step ML-DSA-65 challenge-response P2P auth
- [x] Auth gating: block/tx messages require authentication for v2 peers
- [x] Backwards compatible: v1 peers skip auth, v2 peers negotiate down gracefully
- [x] REGISTER_VALIDATOR tx type — on-chain validator key distribution (ISS-008)
- [x] Validator registry in blockchain + consensus (auto-register genesis validator)
- [x] SQLite validator_registry table for persistent validator storage
- [x] RPC `qv_registerValidator` method for validator self-registration
- [x] Validator registry rollback support in reorg
- [x] Token bucket rate limiting — P2P per-peer (20/s sustained, 100 burst) and RPC per-client (10/s sustained, 50 burst)
- [x] CI expansion: adversarial test suite (46 tests) + integration test suite (29 tests) (ISS-015)
- [x] CI pipeline split into 3 parallel jobs: unit, adversarial, integration with test summary
- [x] 8 Round 13 audit findings fixed (SPRINT1-001, -002, -004, -005, -006, -008, -010, -012)

### v0.2.1
- [x] Pure longest-chain fork resolution (authority scoring removed)
- [x] Protocol versioning (PROTOCOL_VERSION=2, peer classification)
- [x] HTML proof certificate export (`qbit proof --format html`)
- [x] Auto key registration on wallet create (`--register` flag)
- [x] CLI store command (document hash on-chain with local CID)
- [x] CLI share command (ML-KEM-768 encrypted sharing)
- [x] Dockerfile multi-stage build (python:3.11-slim + liboqs)
- [x] docker-compose 3-validator testnet (env-var tokens)
- [x] XSS prevention in HTML proof template
- [x] SQLite chain hash verification on load
- [x] CLI --insecure TLS warning

### v0.2.0
- [x] Fork resolution with try_reorg(), MAX_REORG_DEPTH=32
- [x] Request-ID correlation for P2P block sync
- [x] SQLite ChainStore dual-write persistence
- [x] TLS support for RPC (`--tls-cert/--tls-key`, `--tls-self-signed`)
- [x] O(1) nonce validation + O(1) pool scan
- [x] Notarizations reverse index
- [x] CLI: wallet, notarize, verify, proof, verify-proof
- [x] Proof export with Merkle path for offline verification
- [x] GitHub Actions CI (Python 3.11/3.12)

### v0.1.0
- [x] ML-DSA-65 + ML-KEM-768 + SHA3-256 + AES-256-GCM (liboqs)
- [x] Dual-keypair wallet (signing + encryption) with scrypt+AES encryption
- [x] 4 TX types: NOTARIZE, STORE, SHARE, REGISTER_KEY
- [x] PoA consensus (round-robin), Merkle tree (domain-separated)
- [x] JSON-RPC 2.0 (22 methods, bearer auth, batch, body limits)
- [x] TCP P2P (peer discovery, SSRF protection, MAX_PEERS, HELLO timeout)
- [x] Atomic persistence, chain load validation, 9-round security audit (104 issues)

---

## Planned (v0.4.0)

### Protocol
- [ ] P2P encrypted channel (ML-KEM session key + AES-GCM)
- [ ] Validator staking / deposit mechanism
- [ ] Block finality (checkpoint mechanism)
- [ ] Responder-signs-before-verify protocol fix (SPRINT1-003, deferred from v0.3.0)
- [ ] Genesis validator on-chain REGISTER_VALIDATOR tx (SPRINT1-007, deferred from v0.3.0)

### Consensus
- [ ] Delegated Proof of Stake (dPoS)
- [ ] Multi-validator epoch rotation
- [ ] Slashing for misbehavior

### Storage
- [ ] Chain pruning (ISS-007)
- [ ] Transaction pool persistence
- [ ] Parameterized SQLite queries in validator/revocation tables (SPRINT1-011, deferred from v0.3.0)

### Security
- [ ] Key material zeroing via ctypes/mmap (ISS-001)
- [ ] Peer reputation scoring (ISS-009)
- [ ] ACME/Let's Encrypt TLS auto-provisioning (ISS-016)

### Client
- [ ] IPFS integration for STORE/SHARE workflows
- [ ] Human-readable proof PDF export

### Infrastructure
- [ ] Light client protocol (Merkle proof verification only)
- [ ] Cross-chain bridge (hash anchoring)
