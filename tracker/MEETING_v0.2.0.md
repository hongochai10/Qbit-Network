# QBit Network — Project Meeting: v0.2.0 Planning

**Date:** 2026-03-25
**Attendees:** tech-lead, product-owner, security-auditor, protocol-designer, perf-engineer
**Chair:** tech-lead

---

## 1. Current State (Tech Lead)

### v0.1.0 Strengths
- Crypto layer clean: ML-DSA-65, ML-KEM-768, SHA3-256, AES-256-GCM, all with input validation
- 4 tx types (NOTARIZE, STORE, SHARE, REGISTER_KEY) with payload whitelisting + nonce ordering
- 149 tests passing, 104 audit issues resolved across 9 rounds
- Atomic persistence, bearer auth RPC, P2P with SSRF protection

### Critical Gaps
- **No fork resolution** (ISS-003) — permanent chain divergence with multi-validator
- **No P2P authentication** (ISS-002) — MITM trivial
- **In-memory chain** (ISS-006) — ~3.3 GB at 10K blocks, hard ceiling
- **No TLS on RPC** (ISS-004) — shared secrets over cleartext HTTP
- **No CLI tool** — non-developers cannot use the system

---

## 2. v0.2.0 Requirements (Product Owner)

### User Stories (Priority Order)

**P0-1: CLI Wallet & Notarization Tool**
> As a legal professional, I want to notarize a document from the command line so that I don't need to write curl commands or read source code.

Acceptance: `qbit wallet create`, `qbit notarize <file>`, `qbit verify <file>`, `qbit proof export`

**P0-2: TLS for RPC API**
> As a compliance officer, I want HTTPS so that auth tokens and shared secrets are not exposed on the wire.

Acceptance: `--tls-cert/--tls-key` flags, reject secret-returning methods over HTTP

**P1-3: Persistent Database Backend**
> As a node operator, I want my node to restart without losing the chain.

Acceptance: LevelDB backend, auto-migration from chain.json, bounded memory

**P1-4: Fork Resolution**
> As a multi-validator operator, I want the network to converge after transient partitions.

Acceptance: Longest valid chain rule, max reorg depth 100, displaced txs return to pool

**P1-5: Proof Export & Offline Verification**
> As opposing counsel, I want to verify a notarization proof without running a QBit node.

Acceptance: `qbit verify-proof proof.json` works offline with only liboqs installed

### Definition of Done
1. Non-developer can notarize a PDF and export verifiable proof using only CLI + README
2. RPC supports TLS; secrets never over cleartext
3. Node restarts without data loss; memory bounded
4. Two validators can run without permanent divergence
5. 149+ tests pass, security audit (Round 10) completed

---

## 3. Security Assessment (Security Auditor)

### MUST-FIX Before v0.2.0 (4 gates)

| Gate | Issue | Reason |
|------|-------|--------|
| 1 | **ISS-004** (TLS) | Shared secrets over cleartext = silent key compromise. Fastest fix, highest impact. |
| 2 | **ISS-005** (Request-ID) | 50-line fix eliminates chain-split vector even without full P2P auth. |
| 3 | **ISS-003** (Fork resolution) | Multi-validator without fork resolution = guaranteed chain break. |
| 4 | **ISS-002** (P2P auth) | Validators must verify they're talking to each other. |

### New Risks from v0.2.0 Features
- **NEW-R1 (HIGH)**: LevelDB partial writes → must use WriteBatch for atomicity
- **NEW-R5 (HIGH)**: Self-signed TLS → users disable cert verification → MITM
- **NEW-R8 (HIGH)**: CLI secrets in shell history → always prompt interactively
- **NEW-R11 (CRITICAL)**: Validator key bootstrapping chicken-and-egg in genesis
- **NEW-R12 (HIGH)**: Staking state reentrancy during block validation

### Security Gates
1. Round 10 audit before RC1 (zero CRITICAL/HIGH open)
2. 3-validator testnet with deliberate partitions
3. Red team against full v0.2.0 stack
4. Dependency audit (`pip-audit`)
5. Release checklist sign-off

---

## 4. Architecture Proposals (Protocol Designer)

### Fork Resolution: Authority-Scored Longest Chain
- Each validator assigned authority score based on registration order
- Chain weight = sum of block authority scores (not just length)
- Prevents "longest spam chain" from a single malicious validator
- Max reorg depth: configurable (default 100 blocks)
- Finality checkpoints deferred to v0.3.0

### P2P Authentication: ML-KEM + ML-DSA Handshake
```
Initiator                                    Responder
    |-- HELLO_AUTH { kem_pk, signing_pk, sig } -->|
    |<-- AUTH_RESP { kem_ct, signing_pk, sig } ---|
    |   Both derive: session_key = SHAKE-256(shared_secret || transcript)
    |   All subsequent messages: AES-256-GCM encrypted with counter nonce
```
- 12.7 KB one-time handshake cost per connection
- Uses only existing algorithms (no new deps)
- Forward secrecy via ephemeral ML-KEM
- Backward compatible: accept both `hello` (untrusted) and `hello_auth` (trusted) during transition

### Storage Migration: chain.json → LevelDB
- Phase 1: Abstract behind `ChainStore` interface
- Phase 2: One-time migration tool (validates during import)
- Phase 3: Auto-detection on startup (transparent upgrade)
- Phase 4 (v0.3.0): Drop JSON backend

### Block Format: No Breaking Changes
- Add optional `version` + `authorityScore` fields (not in canonical header)
- Transaction format unchanged
- Breaking changes deferred to v0.3.0 (PoA → dPoS transition)

---

## 5. Performance Analysis (Perf Engineer)

### Memory Projection
```
10K blocks (500K txs):    ~3.3 GB  ← current practical ceiling
100K blocks (5M txs):    ~32.6 GB  ← impossible without DB
With LevelDB:            ~160 MB   ← 200x reduction
```

### Top Bottlenecks
| Priority | Issue | Current | Fix | Improvement |
|----------|-------|---------|-----|-------------|
| P0 | Nonce validation O(n^2) | consensus.py:127 | Precompute sender_first_nonce dict | 200x at max block |
| P0 | Pool scan O(pool_size) | blockchain.py:90 | Maintain `_pool_sender_count` | 10,000x at full pool |
| P1 | `get_all_notarizations` O(all_txs) | blockchain.py:250 | Reverse index `_notarizations_by_hash` | 500,000x at 5M txs |
| P2 | In-memory chain | blockchain.py:20 | LevelDB backend | 32.6 GB → 160 MB |

### Recommended CI Benchmarks
6 benchmarks with regression thresholds: submit_tx, produce_block, validate_block, chain_load, memory_1k_blocks, get_all_notarizations

---

## 6. DECISIONS

### Decision 1: v0.2.0 Scope
**APPROVED.** 5 features: CLI tool, TLS, LevelDB, fork resolution, proof export.

### Decision 2: Fork Resolution Strategy
**APPROVED.** Authority-scored longest chain for v0.2.0. Checkpoint finality deferred to v0.3.0.

### Decision 3: Storage Backend
**APPROVED.** LevelDB via `plyvel`. ChainStore abstraction, auto-migration, keep MemoryStore for tests.

### Decision 4: P2P Auth Scope
**SPLIT DECISION.**
- v0.2.0: Request-ID correlation (ISS-005, ~50 lines) + HELLO_AUTH handshake
- v0.3.0: Full encrypted channel, drop legacy HELLO

### Decision 5: No Breaking Changes
**APPROVED.** Block and transaction formats unchanged. Optional metadata fields only.

---

## 7. SPRINT PLAN (2 Weeks)

### Week 1: Foundation

| Day | Task | Agent | Resolves |
|-----|------|-------|----------|
| 1-2 | Fork resolution protocol spec | `protocol-designer` | ISS-003 spec |
| 1-2 | Abstract `ChainStore` interface | `blockchain-dev` | ISS-006 prep |
| 1-2 | TLS support for RPC (aiohttp ssl_context) | `devops` | ISS-004 |
| 1-2 | 3-node testnet script | `devops` | Test infra |
| 3-4 | LevelDB `ChainStore` backend | `blockchain-dev` | ISS-006 |
| 3-4 | Request-ID correlation for P2P | `blockchain-dev` | ISS-005 |
| 3-4 | CI/CD pipeline (GitHub Actions) | `devops` | CI |
| 5 | Security review of storage + TLS changes | `security-auditor` | Gate 1 |
| 5 | O(n^2) nonce fix + pool scan fix | `perf-engineer` | ISS-011 |

### Week 2: Features + Polish

| Day | Task | Agent | Resolves |
|-----|------|-------|----------|
| 6-7 | Fork resolution implementation | `blockchain-dev` | ISS-003 |
| 6-7 | CLI wallet tool (create, list, notarize, verify) | `frontend-dev` | CLI |
| 8 | Proof export + offline verification | `blockchain-dev` | Proof |
| 8 | HELLO_AUTH handshake (P2P auth) | `blockchain-dev` | ISS-002 partial |
| 9 | Full test suite update + fork adversarial tests | `test-runner` | QA |
| 9 | Round 10 security audit | `security-auditor` | Gate 1 |
| 10 | Docs update, CHANGELOG, paper update | `docs-writer` | Docs |
| 10 | Performance benchmarks (LevelDB vs in-memory) | `perf-engineer` | Bench |

### Sprint Deliverables
- [x] ISS-003 (fork resolution)
- [x] ISS-004 (TLS)
- [x] ISS-005 (request-ID correlation)
- [x] ISS-006 (LevelDB backend)
- [x] ISS-011 (O(n^2) nonce fix)
- [x] ISS-002 partial (HELLO_AUTH handshake)
- [x] CLI wallet tool
- [x] Proof export + offline verification
- [x] CI/CD pipeline
- [x] 3-node testnet
- [x] Round 10 security audit

---

## 8. RISKS

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Fork resolution introduces consensus bugs | HIGH | Chain corruption | Spec-first approach, security review, adversarial tests |
| LevelDB migration breaks index consistency | MEDIUM | Data corruption | Keep MemoryStore as reference, diff outputs |
| Sprint scope creep | MEDIUM | Key features slip | ISS-001, ISS-009, ISS-010 explicitly deferred to v0.3.0 |
| No integration test environment | HIGH | Fork/P2P untestable | 3-node testnet Day 1 priority for devops |

### Hard Blockers
1. **3-node testnet script** must exist before fork resolution testing (Day 1-2 devops)
2. **Fork resolution spec** must be reviewed before implementation (Day 1-2 protocol-designer)

---

## 9. NOT IN v0.2.0 (Deferred)

| Feature | Reason | Target |
|---------|--------|--------|
| Web Dashboard | High effort, low signal before real users | v0.3.0 |
| IPFS Integration | External dependency complexity | v0.3.0 |
| Key Revocation | Needs governance model not yet designed | v0.3.0 |
| Chain Pruning | Only matters after DB backend stable | v0.2.1 |
| Key Material Zeroing | C extension work, doesn't block pilot | v0.2.1 |
| Peer Reputation | Optimization for large networks | v0.3.0 |
| WebSocket Events | No dashboard to consume them | v0.3.0 |
| dPoS Consensus | Major protocol change | v0.3.0 |

---

## Next Steps

1. **Immediately:** `devops` creates 3-node testnet script + CI pipeline
2. **Day 1:** `protocol-designer` delivers fork resolution spec for review
3. **Day 1:** `blockchain-dev` starts ChainStore abstraction
4. **Day 2:** `devops` implements TLS for RPC
5. **Weekly:** `tech-lead` status check, `test-runner` regression suite
