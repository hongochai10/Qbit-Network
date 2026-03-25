# Changelog

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
