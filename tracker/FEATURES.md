# Feature Tracker

## Implemented

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
- [x] 5 TX types: NOTARIZE, STORE, SHARE, REGISTER_KEY, REGISTER_VALIDATOR
- [x] PoA consensus (round-robin), Merkle tree (domain-separated)
- [x] JSON-RPC 2.0 (22 methods, bearer auth, batch, body limits)
- [x] TCP P2P (peer discovery, SSRF protection, MAX_PEERS, HELLO timeout)
- [x] Atomic persistence, chain load validation, 13-round security audit (151+ issues)

---

## Planned (v0.3.0) — Remaining Sprints

### Protocol
- [ ] P2P encrypted channel (ML-KEM session key + AES-GCM)
- [ ] Validator staking / deposit mechanism
- [ ] Key revocation transactions (ISS-010)
- [ ] Block finality (checkpoint mechanism)
- [ ] Responder-signs-before-verify protocol fix (SPRINT1-003, deferred)

### Consensus
- [ ] Delegated Proof of Stake (dPoS)
- [ ] Multi-validator epoch rotation
- [ ] Slashing for misbehavior

### Storage
- [ ] Full SQLite migration (replace in-memory chain list) — includes ISS-012 nonce rename
- [ ] Chain pruning (ISS-007)
- [ ] Transaction pool persistence
- [ ] Parameterized SQLite queries in validator table (SPRINT1-011, deferred)

### Security
- [ ] Key material zeroing via ctypes/mmap (ISS-001)
- [ ] Peer reputation scoring (ISS-009)
- [ ] ACME/Let's Encrypt TLS auto-provisioning (ISS-016)

### Client
- [ ] IPFS integration for STORE/SHARE workflows
- [ ] Web dashboard / chain explorer
- [ ] WebSocket subscriptions (new block, new tx events)
- [ ] Human-readable proof PDF export

### Infrastructure
- [ ] REST API gateway
- [ ] Light client protocol (Merkle proof verification only)
- [ ] Cross-chain bridge (hash anchoring)
