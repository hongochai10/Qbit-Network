# Feature Tracker

## Implemented

### v0.2.1 (current)
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
- [x] Atomic persistence, chain load validation, 12-round security audit

---

## Planned (v0.3.0) — Next Major Release

### Protocol
- [ ] HELLO_AUTH server-side handler + challenge verification (ISS-002)
- [ ] P2P encrypted channel (ML-KEM session key + AES-GCM)
- [ ] Multi-validator key distribution on-chain (ISS-008)
- [ ] Validator staking / deposit mechanism
- [ ] Key revocation transactions (ISS-010)
- [ ] Block finality (checkpoint mechanism)

### Consensus
- [ ] Delegated Proof of Stake (dPoS)
- [ ] Multi-validator epoch rotation
- [ ] Slashing for misbehavior

### Storage
- [ ] Full SQLite migration (replace in-memory chain list)
- [ ] Chain pruning (ISS-007)
- [ ] Transaction pool persistence

### Security
- [ ] Key material zeroing via ctypes/mmap (ISS-001)
- [ ] Peer reputation scoring (ISS-009)
- [ ] Rate limiting per-peer and per-RPC-client
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
