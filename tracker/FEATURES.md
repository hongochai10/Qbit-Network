# Feature Tracker

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

## Planned (v0.2.0)

### Protocol
- [ ] Multi-validator key distribution on chain
- [ ] Validator staking / deposit mechanism
- [ ] Fork resolution (longest valid chain rule)
- [ ] Block finality (checkpoint mechanism)
- [ ] Key revocation transactions

### Storage
- [ ] LevelDB/RocksDB backend (replace in-memory chain)
- [ ] Chain pruning (configurable retention)
- [ ] Transaction pool persistence
- [ ] State snapshot/restore

### Networking
- [ ] Noise Protocol for P2P authentication
- [ ] Peer reputation scoring
- [ ] NAT traversal / hole punching
- [ ] Request-response correlation (prevent unsolicited blocks)

### Security
- [ ] TLS for RPC server
- [ ] Key material zeroing via ctypes/mmap
- [ ] Rate limiting per-peer and per-RPC-client
- [ ] Audit log on-chain (validator accountability)

### Client
- [ ] CLI tool for wallet management
- [ ] File notarization helper (auto SHA3 + submit)
- [ ] IPFS integration for STORE/SHARE workflows
- [ ] Web dashboard for chain explorer

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
