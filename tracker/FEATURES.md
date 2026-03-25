# Feature Tracker

## Implemented (v0.2.1)

### Protocol (v0.2.1)
- [x] HELLO_AUTH P2P handshake (ML-DSA-65 challenge-response)
- [x] Protocol versioning (PROTOCOL_VERSION=2)
- [x] Pure longest-chain fork resolution (authority scoring removed)
- [x] Peer authenticated/protocol_version classification

### Client (v0.2.1)
- [x] HTML proof certificate export (`--format html`)
- [x] Auto key registration (`--register` flag on wallet create)
- [x] CLI store command (document hash on-chain)
- [x] CLI share command (ML-KEM encrypted sharing)
- [x] Full CLI: 7 commands (wallet, notarize, verify, proof, store, share, verify-proof)

### Infrastructure (v0.2.1)
- [x] Dockerfile (multi-stage, python:3.11-slim + liboqs)
- [x] docker-compose.yml (3-validator testnet)

### Protocol (v0.2.0)
- [x] Fork resolution with try_reorg(), MAX_REORG_DEPTH=32
- [x] Request-ID correlation for P2P block sync (ISS-005)
- [x] SQLite ChainStore dual-write backend
- [x] TLS support for RPC (`--tls-cert/--tls-key`, `--tls-self-signed`)
- [x] O(1) nonce validation (sender_first_nonce)
- [x] O(1) pool scan (_pool_sender_count)
- [x] Notarizations reverse index (_notarizations_by_hash)

### Client (v0.2.0)
- [x] CLI wallet create/list + notarize + verify + proof + verify-proof
- [x] Proof export with Merkle path for offline verification

### Infrastructure (v0.2.0)
- [x] GitHub Actions CI (Python 3.11/3.12, liboqs, pytest)

## Implemented (v0.1.0)

### Core
- [x] ML-DSA-65 digital signatures via liboqs
- [x] ML-KEM-768 key encapsulation via liboqs
- [x] SHA3-256 / SHAKE-256 hashing
- [x] AES-256-GCM authenticated encryption
- [x] Domain-separated Merkle tree with proofs
- [x] Dual-keypair wallet (signing + encryption)
- [x] Wallet encryption (scrypt + AES-256-GCM)
- [x] Address derivation (qv1 + SHA3-256)

### Transaction Types
- [x] NOTARIZE — document hash timestamping
- [x] STORE — encrypted vault entry with IPFS CID
- [x] SHARE — ML-KEM encrypted data sharing with expiry
- [x] REGISTER_KEY — on-chain encryption key binding

### Blockchain
- [x] Block production with ML-DSA signed headers
- [x] Proof of Authority consensus (round-robin)
- [x] Nonce-based replay prevention (per-sender, per-chain)
- [x] Cross-block transaction dedup
- [x] Monotonic timestamps
- [x] Self-validation before block commit
- [x] Atomic chain persistence (tempfile + replace)
- [x] Chain load with full validation

### Networking
- [x] TCP P2P with newline-delimited JSON
- [x] Peer discovery (gossip-based)
- [x] Chain sync (height exchange + block request)
- [x] SSRF protection (private IP blocking, blocked ports)
- [x] MAX_PEERS enforcement
- [x] Inbound HELLO timeout (idle socket DoS prevention)

### RPC API
- [x] JSON-RPC 2.0 with batch support
- [x] Bearer token authentication
- [x] Body size + batch limits
- [x] 22 RPC methods (11 public, 11 protected)
- [x] Type validation on all params
- [x] Error message sanitization

### Security
- [x] 9-round security audit (104 issues resolved)
- [x] Constant-time token comparison
- [x] Payload key whitelisting
- [x] Input size limits at every boundary

## Implemented (v0.2.0)

### Protocol
- [x] Fork resolution (longest valid chain rule)
- [x] Request-response correlation (prevent unsolicited blocks)

### Storage
- [x] LevelDB/SQLite backend (replace in-memory chain)

### Security
- [x] TLS for RPC server (reverse-proxy mode; in-process TLS deferred)

### Performance
- [x] O(n^2) → O(n) consensus nonce validation (precomputed sender-count map)

### Client
- [x] CLI tool for wallet management and notarization
- [x] Merkle proof export

### Infrastructure
- [x] CI/CD pipeline (unit test suite on push)

## Planned (v0.2.x / backlog)

### Protocol
- [ ] Multi-validator key distribution on chain
- [ ] Validator staking / deposit mechanism
- [ ] Block finality (checkpoint mechanism)
- [ ] Key revocation transactions

### Storage
- [ ] Chain pruning (configurable retention)
- [ ] Transaction pool persistence
- [ ] State snapshot/restore

### Networking
- [ ] Noise Protocol for P2P authentication
- [ ] Peer reputation scoring
- [ ] NAT traversal / hole punching

### Security
- [ ] Key material zeroing via ctypes/mmap
- [ ] Rate limiting per-peer and per-RPC-client
- [ ] Audit log on-chain (validator accountability)
- [ ] In-process TLS (no reverse-proxy dependency)

### Client
- [ ] File notarization helper (auto SHA3 + submit)
- [ ] IPFS integration for STORE/SHARE workflows
- [ ] Web dashboard for chain explorer
- [ ] CLI coverage for STORE and SHARE workflows

## Planned (v0.3.0)

### Consensus
- [ ] Delegated Proof of Stake (dPoS)
- [ ] Multi-validator epoch rotation
- [ ] Slashing for misbehavior

### Interop
- [ ] REST API gateway
- [ ] WebSocket subscriptions (new block, new tx)
- [ ] Light client protocol (Merkle proof verification only)
- [ ] Cross-chain bridge (hash anchoring)
