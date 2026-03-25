# QBit Network: A Post-Quantum Cryptographic Blockchain for Document Notarization and Encrypted Data Sharing

**Authors:** QBit Network Development Team

**Date:** March 2026

**Version:** 1.0

---

## Abstract

We present QBit Network, a purpose-built blockchain system that replaces all quantum-vulnerable cryptographic primitives with NIST-standardized post-quantum algorithms. QBit Network utilizes ML-DSA-65 (CRYSTALS-Dilithium) for digital signatures and ML-KEM-768 (CRYSTALS-Kyber) for key encapsulation, providing quantum-resistant document notarization, encrypted vault storage, and secure data sharing. The system implements a dual-keypair identity model, Delegated Proof of Stake (dPoS) consensus with epoch rotation and slashing, and a domain-separated Merkle tree construction. Version 0.4.0 completes the feature set with dPoS stake-weighted validator selection, epoch-based rotation, double-sign slashing, ML-KEM-768 encrypted P2P channels, ctypes-backed secure key material zeroing, TLS auto-provisioning, peer reputation scoring, chain pruning, IPFS integration, and a web dashboard for chain exploration. Through fifteen rounds of security auditing encompassing 190+ identified vulnerabilities, all of which are resolved, we demonstrate that a production-grade PQC blockchain can be realized with acceptable performance overhead. Our implementation achieves 0.29ms per ML-DSA signature, 3.8ms for block production with 50 transactions, and a per-transaction wire overhead of approximately 10.9KB — a 55x increase over ECDSA-based systems, which we argue is an acceptable trade-off for quantum resistance.

**Keywords:** post-quantum cryptography, blockchain, ML-DSA, ML-KEM, document notarization, key encapsulation, CRYSTALS-Dilithium, CRYSTALS-Kyber

---

## 1. Introduction

### 1.1 The Quantum Threat to Blockchain

Current blockchain systems — Bitcoin, Ethereum, Solana, and others — rely on the Elliptic Curve Digital Signature Algorithm (ECDSA) over the secp256k1 curve for transaction authentication and address derivation. Shor's algorithm, when executed on a sufficiently large quantum computer, can solve the Elliptic Curve Discrete Logarithm Problem (ECDLP) in polynomial time, rendering ECDSA signatures forgeable. This would allow an adversary to derive private keys from public keys, enabling unauthorized fund transfers and transaction forgery.

While the timeline for cryptographically relevant quantum computers (CRQC) remains debated, the "harvest now, decrypt later" threat model is immediately relevant for long-lived data. Blockchain records are immutable by design — data committed today will be exposed to quantum attacks for decades. For document notarization systems, where the integrity of timestamped proofs may be challenged years after creation, quantum resistance is not a future concern but a present requirement.

### 1.2 NIST Post-Quantum Standards

In August 2024, the National Institute of Standards and Technology (NIST) published three post-quantum cryptographic standards:

- **FIPS 203** (ML-KEM, based on CRYSTALS-Kyber): A key encapsulation mechanism providing IND-CCA2 security based on the Module Learning With Errors (MLWE) problem.
- **FIPS 204** (ML-DSA, based on CRYSTALS-Dilithium): A digital signature scheme providing EUF-CMA security based on the Module Learning With Errors and Module Short Integer Solution (MLWE/MSIS) problems.
- **FIPS 205** (SLH-DSA, based on SPHINCS+): A hash-based stateless signature scheme providing an alternative security basis.

QBit Network adopts ML-DSA-65 (NIST Security Level 3) and ML-KEM-768 (NIST Security Level 3) as its core cryptographic primitives, combined with SHA-3/SHAKE-256 for hashing (already quantum-resistant under Grover's algorithm with 256-bit outputs).

### 1.3 Contributions

This paper presents:

1. **A complete PQC blockchain architecture** replacing all quantum-vulnerable primitives (ECDSA, ECDH) with NIST-standardized alternatives (ML-DSA, ML-KEM).
2. **A dual-keypair identity model** separating signing (ML-DSA) from encryption (ML-KEM) responsibilities, with an on-chain key registry binding encryption keys to addresses.
3. **A domain-specific transaction model** optimized for document notarization, encrypted storage, and ML-KEM-based secure sharing — including on-chain validator key distribution and permanent key revocation.
4. **A ML-DSA-65 mutual authentication protocol** for P2P connections, providing a 3-step challenge-response handshake that resists downgrade, impersonation, and cross-protocol signature reuse.
5. **A comprehensive security analysis** from fifteen independent audit rounds covering cryptographic, protocol, network, and implementation security, with 0 open findings.
6. **Performance benchmarks** quantifying the overhead of PQC primitives in a blockchain context.

---

## 2. Background and Related Work

### 2.1 Post-Quantum Cryptography in Blockchain

Several research efforts have explored integrating PQC into blockchain systems:

- **NIST PQC Migration Guidelines** (SP 800-227, 2024) recommend organizations begin transitioning to PQC algorithms, with particular urgency for long-lived data.
- **Quantum-resistant ledger (QRL)** was among the first to deploy hash-based signatures (XMSS) in a production blockchain, though XMSS is stateful and requires careful key management.
- **Ethereum's EIP-7702** and related proposals discuss account abstraction that could enable PQC signature schemes, but no mainnet deployment exists.

QBit Network differs from prior work by adopting the final NIST standards (ML-DSA/ML-KEM) rather than intermediate candidates, and by combining signatures with key encapsulation for a complete privacy-preserving document management system.

### 2.2 CRYSTALS-Dilithium (ML-DSA)

ML-DSA is a lattice-based signature scheme derived from the "Fiat-Shamir with Aborts" paradigm. For security level 3 (ML-DSA-65):

| Parameter | Size |
|-----------|------|
| Public key | 1,952 bytes |
| Secret key | 4,032 bytes |
| Signature | 3,309 bytes |

The security is based on the hardness of the Module-LWE and Module-SIS problems, which are believed resistant to both classical and quantum attacks.

### 2.3 CRYSTALS-Kyber (ML-KEM)

ML-KEM is an IND-CCA2-secure key encapsulation mechanism. For ML-KEM-768:

| Parameter | Size |
|-----------|------|
| Public key | 1,184 bytes |
| Secret key | 2,400 bytes |
| Ciphertext | 1,088 bytes |
| Shared secret | 32 bytes |

The encapsulated shared secret is suitable as a symmetric key for AES-256-GCM, enabling hybrid encryption of arbitrary-length data.

---

## 3. System Architecture

### 3.1 Design Principles

QBit Network is designed around four principles:

1. **Quantum-safe by default**: All authentication and key exchange use NIST PQC standards. No classical fallbacks.
2. **Data off-chain, proof on-chain**: Only cryptographic hashes and IPFS content identifiers are stored on-chain. Encrypted data resides on decentralized storage (IPFS).
3. **Zero-knowledge to chain**: The blockchain never observes plaintext. All encryption occurs client-side.
4. **Purpose-built**: Unlike general-purpose smart contract platforms, QBit Network is optimized for three operations: notarize, store, and share.

### 3.2 Identity Model

Each participant holds two independent PQC keypairs:

**ML-DSA keypair** (signing): Used to authenticate transactions and prove identity. The address is derived as:

```
address = "qv1" || Hex(SHA3-256(ML-DSA public key))
```

This produces a 67-character address (3-character prefix + 64 hexadecimal characters), analogous to Ethereum's address derivation from ECDSA public keys.

**ML-KEM keypair** (encryption): Used to receive encrypted data via key encapsulation. The encryption public key is registered on-chain through a `REGISTER_KEY` transaction, creating a verifiable binding between an address and its encryption capability.

This separation follows the principle of key-use separation recommended by NIST SP 800-57, preventing a compromised signing key from exposing encrypted data.

### 3.3 Transaction Types

QBit Network defines ten transaction types:

**NOTARIZE**: Creates an immutable timestamp proof that a document (identified by its SHA3-256 hash) existed at the transaction's timestamp. The first notarization of a given hash is preserved as the canonical proof; subsequent notarizations by other parties are recorded but do not overwrite the first.

**STORE**: Records an encrypted vault entry, associating a document hash with an IPFS Content Identifier (CID) and optional encrypted metadata. The CID references encrypted data stored off-chain.

**SHARE**: Implements ML-KEM-based encrypted data sharing:

1. The sender encapsulates a shared secret using the recipient's ML-KEM public key, producing a ciphertext.
2. The sender encrypts the data with AES-256-GCM using the shared secret as the key.
3. The sender uploads the encrypted data to IPFS, obtaining a CID.
4. The transaction records the CID and ML-KEM ciphertext on-chain.
5. The recipient decapsulates the ciphertext using their ML-KEM secret key to recover the shared secret, then decrypts the data.

SHARE transactions include an optional `expires` field (Unix timestamp) after which `get_shared_with` queries filter them out.

**REGISTER_KEY**: Publishes an ML-KEM encryption public key on-chain, binding it to the sender's address. The system maintains a version history of all registered keys, ensuring that shares encrypted to previous keys remain identifiable.

**REGISTER_VALIDATOR**: Distributes an ML-DSA-65 validator public key on-chain, enabling all nodes to verify block signatures without out-of-band key exchange. The payload includes the validator's ML-DSA public key and the derived address (verified to match). Duplicate registrations are rejected. The genesis validator is auto-registered in memory; subsequent validators join via this transaction.

**REVOKE_KEY**: Permanently revokes a signing, encryption, or validator key on-chain. Self-revocation only. Revoking a signing key immediately blocks the address from submitting further transactions. Revoking a validator key removes the validator from the active consensus set. The genesis validator cannot be revoked. Revocations are rolled back during chain reorganization.

**STAKE**: Self-stakes weight on the sender's own validator address. The amount must be between MIN_STAKE (1) and MAX_STAKE (1,000,000). The target validator must be registered and not slashed.

**DELEGATE**: Delegates stake weight from any address to a registered validator. Enables community participation in consensus without running a validator node.

**UNSTAKE**: Initiates unbonding of staked tokens. The stake is returned after UNBONDING_PERIOD (100 blocks), preventing immediate withdrawal after misbehavior.

**EVIDENCE**: Reports validator double-signing by providing two valid ML-DSA-65 signatures over different block hashes at the same block index. Triggers slashing: all stakers' positions reduced by SLASH_PERCENTAGE (50%). Duplicate evidence for the same validator is rejected.

### 3.4 Transaction Structure

Each transaction contains:

| Field | Description |
|-------|-------------|
| `type` | NOTARIZE, STORE, SHARE, REGISTER_KEY, REGISTER_VALIDATOR, or REVOKE_KEY |
| `from` | Sender address (qv1...) |
| `to` | Recipient address (SHARE only) |
| `timestamp` | Unix timestamp |
| `nonce` | Per-sender sequential counter |
| `chainId` | Chain identifier (replay protection) |
| `payload` | Type-specific data (whitelisted keys) |
| `signature` | ML-DSA signature over canonical JSON |
| `sender_pubkey` | ML-DSA public key (1,952 bytes) |

The transaction ID is `SHA3-256(canonical_json(signable_fields))`. The canonical form uses sorted keys with minimal separators to ensure deterministic serialization.

### 3.5 Block Structure

Blocks contain:

| Field | Description |
|-------|-------------|
| `index` | Sequential block number |
| `timestamp` | Must be strictly greater than parent |
| `prevHash` | SHA3-256 of parent block header |
| `merkleRoot` | Domain-separated Merkle root of transaction IDs |
| `validator` | Address of the block-producing validator |
| `signature` | ML-DSA signature over canonical header |

### 3.6 Merkle Tree Construction

QBit Network implements a domain-separated Merkle tree to prevent second-preimage attacks:

```
Leaf:     H(0x00 || tx_id)
Internal: H(0x01 || left || right)
Odd:      Promoted without duplication
```

Where H = SHA3-256. The domain separation byte (`0x00` for leaves, `0x01` for internal nodes) ensures that a leaf hash can never equal an internal node hash, preventing the well-known vulnerability where `[A, B, C]` and `[A, B, C, C]` produce the same root in naive implementations.

### 3.7 Consensus: Delegated Proof of Stake (dPoS)

QBit Network employs Delegated Proof of Stake with epoch-based rotation:

**Validator Selection (dPoS mode):**
```
seed = SHA3-256(parent_hash || ":" || block_index)
validator = weighted_random_selection(sorted_validators, seed)
```

When no validators have stake, the system falls back to PoA round-robin:
```
validator(block_index) = sorted_validators[block_index mod n]
```

**Epoch Rotation:** Every EPOCH_LENGTH (100) blocks, the active validator set is frozen. Stake changes during an epoch take effect at the next epoch boundary. This prevents mid-epoch validator set manipulation.

**Slashing:** Validators who double-sign (produce two blocks at the same height with different hashes) can be reported via EVIDENCE transactions. Slashing reduces all stakers' positions by SLASH_PERCENTAGE (50%). Validators whose total stake drops below MIN_STAKE are removed from the active set.

**Staking Model:** Three transaction types manage stake:
- **STAKE**: Self-stake weight on own validator (amount 1 to 1,000,000)
- **DELEGATE**: Delegate stake weight to any registered validator
- **UNSTAKE**: Begin unbonding (effective after 100-block UNBONDING_PERIOD)

Block validation enforces:
- Sequential block indices with valid parent hash linkage
- Monotonically increasing timestamps with a 30-second future drift limit
- Correct validator selection (dPoS weighted or PoA round-robin)
- Valid ML-DSA block signature
- All transaction signatures valid
- Sequential per-sender nonces (both within the block and against chain state)
- No cross-block transaction replay (maintained via `_chain_tx_ids` set)
- Block size bounded (estimated from transaction count)
- No empty non-genesis blocks
- EVIDENCE transactions: both signatures valid, different block hashes, same index

Self-produced blocks undergo full consensus validation before commitment, preventing chain divergence from same-second timestamp collisions.

---

## 4. Security Analysis

### 4.1 Audit Methodology

The system underwent thirteen rounds of security auditing:

| Round | Focus | Issues Found |
|-------|-------|-------------|
| 1 | Basic correctness and logic bugs | 14 |
| 2 | Cryptographic implementation review | 21 |
| 3 | Line-by-line code review | 16 |
| 4 | Regression analysis (fix-induced bugs) | 3 |
| 5 | Automated security agent analysis | 21 |
| 6 | Red team adversarial testing | 9 |
| 7 | Fix regression and edge case analysis | 11 |
| 8 | Cross-module consistency verification | 4 |
| 9 | Semantic and protocol correctness | 5 |
| 10 | v0.2.0 feature audit (fork, TLS, CLI, store) | 7 |
| 11 | Rate limiting and auth baseline | 9 |
| 12 | v0.2.1 full audit (P2P auth, Docker, store/share) | 9 |
| 13 Sprint 1 | v0.3.0 Sprint 1 (HELLO_AUTH, REGISTER_VALIDATOR, rate limiting, CI) | 14 |
| 13 Sprint 2 | v0.3.0 Sprint 2 (SQLite-primary, REVOKE_KEY, REST API, WebSocket) | 16 |
| 14 | v0.4.0 Sprint 1-2 (dPoS, epochs, slashing, P2P encryption, connection dedup) | 9 |
| 15 | v0.4.0 Sprint 3 (SecureBytes, TLS auto-provisioning, reputation, pruning) | 5 |
| **Total** | | **190+** |

### 4.2 Cryptographic Security

**Signature scheme**: ML-DSA-65 provides EUF-CMA security at NIST Level 3 (equivalent to AES-192 against quantum attacks). The implementation wraps liboqs with exception handling on both sign and verify paths, preventing node crashes from malformed inputs.

**Key encapsulation**: ML-KEM-768 provides IND-CCA2 security at NIST Level 3. Input validation enforces exact sizes (pk=1184, sk=2400, ct=1088 bytes) before passing to the C library.

**Symmetric encryption**: AES-256-GCM with random 12-byte nonces provides authenticated encryption. Minimum ciphertext length (28 bytes = 12 nonce + 16 tag) is validated before decryption.

**Hashing**: SHA3-256 provides 128-bit quantum security (via Grover's algorithm), sufficient for address derivation and Merkle tree construction.

**Key derivation**: Wallet encryption uses scrypt (N=16384, r=8, p=1) with enforced minimum and maximum parameters to prevent both downgrade attacks (attacker sets n=1) and DoS attacks (attacker sets n=2^30).

**Token comparison**: RPC authentication uses `hmac.compare_digest` for constant-time comparison, preventing timing side-channel attacks.

### 4.3 P2P Authentication Protocol

Prior to v0.3.0, P2P connections were unauthenticated — any node could announce blocks or transactions without proving identity. Version 0.3.0 introduces a 3-step ML-DSA-65 mutual challenge-response handshake:

1. **hello_auth** (Initiator → Responder): Initiator sends its ML-DSA public key, derived node address, and a 32-byte random challenge.
2. **auth_response** (Responder → Initiator): Responder signs the initiator's challenge and issues its own counter-challenge.
3. **auth_confirm** (Initiator → Responder): Initiator signs the counter-challenge; both sides mark the connection authenticated.

Signatures use a domain-separated prefix `"QBIT_AUTH_v2:" || challenge || signer_address` to prevent cross-protocol reuse. Challenges are single-use and cryptographically random. Auth deadlines use `time.monotonic()` to resist wall-clock skew attacks. Failed authentication triggers immediate disconnect with no fallback to unauthenticated state. Block and transaction messages from unauthenticated v2 peers are rejected after the grace period.

### 4.4 Multi-Validator Key Distribution

In earlier versions, validator public keys were distributed out-of-band at node startup. This creates a bootstrapping problem: nodes that join after genesis must be manually configured with each validator's public key. Version 0.3.0 solves this with the `REGISTER_VALIDATOR` transaction type. Validators publish their ML-DSA-65 public keys on-chain, where any node can discover and verify them without out-of-band communication. The registry is persisted in SQLite and fully rolled back on chain reorganization. Duplicate registrations are rejected to prevent key substitution attacks.

### 4.5 Protocol Security

**Replay protection**: Transactions include a `chainId` field and per-sender nonces. Cross-block replay is prevented by the `_chain_tx_ids` set checked during consensus validation.

**Genesis protection**: The genesis block hash is locked after initialization. Non-validator nodes accept genesis from the first sync peer, then lock immediately via `_lock_genesis_if_needed()`.

**Block ordering**: The `add_block()` function enforces `block.index == len(chain)`, preventing out-of-order injection that could corrupt state indices.

**Notarization immutability**: The first notarization of a document hash is preserved; subsequent notarizations cannot overwrite it. A `get_all_notarizations()` API returns all parties who notarized the same hash.

**Key revocation**: Compromised or rotated keys can be revoked permanently via `REVOKE_KEY` transactions. Revoked signing keys are blocked from further transaction submission at both the RPC and consensus layers. This closes a gap where a leaked signing key could remain active indefinitely.

### 4.6 Network Security

**SSRF prevention**: The `_is_safe_peer()` function blocks connections to private RFC 1918 ranges (configurable), link-local addresses, reserved ranges, cloud metadata endpoints, and common service ports (SSH, MySQL, etc.).

**Connection limits**: MAX_PEERS=50 enforced on both inbound and outbound. Inbound connections require HELLO within 10 seconds or are disconnected.

**Message limits**: P2P reader limit = 10MB. RPC body limit = 1MB (enforced at aiohttp level, handles chunked encoding). Batch requests capped at 50.

**Data canonicalization**: P2P re-broadcasts use `block.to_dict()` / `tx.to_dict()` canonical forms, not raw peer data, preventing injection of extra fields.

### 4.7 Concurrency Safety

All transaction-creating RPC endpoints acquire a per-address `asyncio.Lock` before computing the nonce, signing, and submitting. This prevents concurrent requests from producing duplicate nonces.

### 4.8 Persistence Security

Both chain and wallet files use atomic writes (tempfile + `os.replace`). Wallet files are written with 0o600 permissions set before the atomic rename (no TOCTOU window). Chain loading validates all blocks in a temporary list before committing (all-or-nothing), preventing partial corruption on load failure.

### 4.9 Known Limitations

| Limitation | Impact | Mitigation Path |
|------------|--------|-----------------|
| No transaction pool persistence | Pending TXs lost on crash | WAL-based pool persistence (v0.5.0+) |
| No block finality | No checkpoint mechanism | Checkpoint protocol (v0.5.0+) |
| Sybil resistance is probabilistic | High-stake adversary can influence block selection | Bonding requirements and slashing raise cost; formal finality would close this gap |

---

## 5. Implementation

### 5.1 Technology Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.11+ |
| PQC Library | liboqs 0.15.0 via liboqs-python |
| Symmetric Crypto | `cryptography` library (AES-GCM) |
| Hashing | `hashlib` (SHA3-256, SHAKE-256) |
| Networking | `asyncio` (P2P), `aiohttp` (RPC) |
| KDF | `hashlib.scrypt` |

### 5.2 Code Metrics

| Metric | Value |
|--------|-------|
| Source code (qbit_network/) | ~4,800 lines |
| Test code (tests/) | ~5,200 lines |
| Test cases | 1,080 |
| Test modules | 14 (crypto, wallet, transaction, block, blockchain, adversarial, integration, dPoS, REST API, WebSocket, IPFS, TLS, SecureBytes, reputation) |
| Documentation | ~2,800 lines across 9 files |
| Security issues found/fixed | 190+ across 15 audit rounds |

### 5.3 Module Architecture

```
qbit_network/
├── crypto/              Zero-dependency PQC primitives
│   ├── mldsa.py         ML-DSA-65 (sign, verify, keygen)
│   ├── mlkem.py         ML-KEM-768 (encapsulate, decapsulate, keygen)
│   ├── hashing.py       SHA3-256, SHAKE-256, Merkle tree
│   ├── aes.py           AES-256-GCM
│   └── secure_bytes.py  ctypes-backed mutable key material with zero()
├── core/                Blockchain state machine
│   ├── wallet.py        Dual-keypair identity, scrypt+AES encryption, SecureBytes
│   ├── transaction.py   10 TX types with validation
│   ├── block.py         Block structure with Merkle proofs
│   ├── blockchain.py    Chain management, dPoS, epochs, slashing, pruning
│   ├── consensus.py     dPoS weighted selection + PoA round-robin fallback
│   ├── store.py         SQLite backend (blocks, validators, stakes, epochs, slashing)
│   └── proof.py         Merkle proof export + block signature verification
├── network/             Communication
│   ├── p2p.py           TCP P2P, HELLO_AUTH 3-step auth, ML-KEM encrypted channel
│   ├── rpc.py           JSON-RPC 2.0 with bearer auth, WebSocket attach, dashboard
│   ├── rest_api.py      REST API gateway (36 endpoints, sub-app pattern)
│   ├── websocket.py     WebSocket pub/sub (3 channels, WebSocketManager)
│   ├── rate_limiter.py  Token bucket rate limiting (P2P + RPC)
│   ├── tls_manager.py   TLS auto-provisioning, renewal, hot-reload
│   └── reputation.py    PeerReputation scoring with decay and banning
└── node.py              Full node orchestrator
```

Dependencies flow strictly downward: `crypto` has zero internal dependencies; `core` depends only on `crypto`; `network` depends on `core`; `node` orchestrates all layers.

Dependencies flow strictly downward: `crypto` has zero internal dependencies; `core` depends only on `crypto`; `network` depends on `core`; `node` orchestrates all layers.

---

## 6. Performance Evaluation

### 6.1 Cryptographic Operation Benchmarks

Measured on Apple Silicon (M-series), Python 3.11, liboqs 0.15.0:

| Operation | Latency | Comparison to ECDSA |
|-----------|---------|-------------------|
| ML-DSA-65 keygen | 0.2 ms | ~2x slower than secp256k1 |
| ML-DSA-65 sign | 0.29 ms | ~3x slower |
| ML-DSA-65 verify | 0.06 ms | ~1.5x slower |
| ML-KEM-768 keygen | < 0.1 ms | Comparable to ECDH |
| ML-KEM-768 encaps | 0.01 ms | Faster than ECDH |
| ML-KEM-768 decaps | 0.02 ms | Comparable to ECDH |
| Wallet generation | 0.1 ms | ~2x (dual keypair) |
| Block production (50 tx) | 3.8 ms | Dominated by signing |

ML-DSA signing at 0.29ms enables throughput of approximately 3,400 signatures per second per core. Block production at 3.8ms for 50 transactions is well within the 5-second block interval.

### 6.2 Size Overhead Analysis

| Component | Classical (ECDSA) | QBit Network (ML-DSA-65) | Overhead |
|-----------|------------------|-------------------|----------|
| Public key | 33 bytes | 1,952 bytes | 59x |
| Secret key | 32 bytes | 4,032 bytes | 126x |
| Signature | 64 bytes | 3,309 bytes | 52x |
| Transaction | ~200 bytes | ~10,900 bytes | 55x |
| Block (50 tx) | ~12 KB | ~552 KB | 46x |

The per-transaction overhead of approximately 10.9KB is dominated by the ML-DSA signature (3,309 bytes hex-encoded = 6,618 characters) and public key (1,952 bytes hex-encoded = 3,904 characters).

### 6.3 ML-KEM Overhead for SHARE Transactions

| Component | ECDH | ML-KEM-768 | Overhead |
|-----------|------|-----------|----------|
| Encapsulated key | 33 bytes | 1,088 bytes | 33x |
| Shared secret | 32 bytes | 32 bytes | 1x |
| Encapsulation time | ~0.1 ms | 0.01 ms | 0.1x (faster) |

ML-KEM-768 is notably faster than ECDH for key encapsulation while providing quantum resistance. The size overhead is significant but acceptable for per-share operations.

### 6.4 Storage Impact

With a 5-second block interval and 50 transactions per block, daily chain growth is approximately:

```
Blocks/day = 86,400 / 5 = 17,280
Growth/day = 17,280 × 552 KB ≈ 9.3 GB
```

This is substantial compared to classical blockchains (~1-2 GB/day for Ethereum). For a notarization chain with lower transaction volume (e.g., 10 tx/block average), daily growth reduces to approximately 1.9 GB — comparable to Ethereum's historical growth rate.

---

## 7. Test Coverage

### 7.1 Test Categories

| Category | Tests | Coverage |
|----------|-------|----------|
| Cryptographic primitives | 30 | ML-DSA (8), ML-KEM (5), SHA3 (5), AES-GCM (7), Merkle (12 incl. parametrized 1-16 items) |
| Wallet management | 19 | Generation (6), serialization (2), persistence (11 incl. encryption, tamper, scrypt) |
| Transaction processing | 27 | Creation (6), signing (4), serialization (6), payload validation (11) |
| Block handling | 14 | Creation (3), signing (3), serialization (5), Merkle proofs (3) |
| Blockchain state | 40 | Genesis (2), pool (8), production (4), reception (3), nonce (2), notarization (3), key registry (2), expiry (2), persistence (5) |
| Adversarial scenarios | 19 | Chain attacks (3), replay (2), payload (2), crypto (3), SSRF (7), wallet (2) |

### 7.2 Adversarial Test Scenarios

The adversarial test suite specifically validates defenses against:

- Genesis block replay and chain fork attacks
- Future timestamp injection (chain freeze)
- Cross-chain replay (chain_id differentiation)
- Nonce gap injection in blocks
- Payload key injection (dedup bypass)
- Oversized payload submission
- Malformed ML-DSA signatures (1-byte sig, wrong pk size)
- SSRF via 7 attack vectors (private IPs, link-local, loopback, reserved ports, self-connect, negative port, metadata hostnames)
- Wallet scrypt parameter DoS
- Ciphertext truncation attack

---

## 8. Discussion

### 8.1 PQC Migration Trade-offs

The primary cost of quantum resistance is size: a 55x increase in per-transaction overhead. However, this trade-off is favorable for several reasons:

1. **Storage is cheap, quantum attacks are not**: At current storage costs (~$0.02/GB), the additional 10KB per transaction costs negligible compared to the value of quantum-resistant guarantees.
2. **QBit Network stores proofs, not data**: The off-chain storage model (IPFS for encrypted blobs) means on-chain growth is bounded by transaction count, not data volume.
3. **ML-DSA is computationally efficient**: Despite larger outputs, ML-DSA's signing and verification speeds are within 3x of ECDSA, ensuring that PQC does not become a throughput bottleneck.

### 8.2 Dual-Keypair Model Justification

The separation of ML-DSA (signing) and ML-KEM (encryption) follows NIST SP 800-57 key-use separation recommendations. This provides:

- **Independent compromise scope**: A compromised signing key does not expose encrypted data.
- **Algorithm agility**: Signing and encryption keys can be upgraded independently as PQC standards evolve.
- **On-chain key discovery**: The `REGISTER_KEY` transaction enables participants to discover each other's encryption keys without out-of-band exchange.

### 8.3 Security Hardening Through Iterative Audit

The fifteen-round audit process demonstrates the value of iterative security review:

- **Round 1-3** (manual review): Found fundamental issues — broken serialization, XOR encryption, missing validation.
- **Round 4-5** (regression + automated): Found fix-induced bugs and systematic weaknesses — out-of-order block injection, SSRF via peer gossip.
- **Round 6-7** (red team + edge cases): Found concurrency bugs and protocol-level attacks — nonce race conditions, genesis injection.
- **Round 8-9** (consistency + semantic): Found subtle correctness issues — notarization overwrite, produce_block bypassing consensus.
- **Round 10-11** (feature + rate limiting): Found feature-scope issues — unbounded reorg depth, SQLite partial failure, rate limiter shared-state race.
- **Round 12** (v0.2.1 full audit): Found validator registry overwrite, XSS in HTML proof export, genesis validator not in SQLite.
- **Round 13 Sprint 1-2** (v0.3.0): Found auth grace period bypass, v1 downgrade on failed auth, handshake flood, rate limiter LRU eviction bypass — all fixed.
- **Rounds 14-15** (v0.4.0): Found dPoS seed bug causing deterministic selection, duplicate connection slot exhaustion, EVIDENCE size limit bypass, TLS non-atomic cert renewal, SecureBytes `__del__` AttributeError on failed init — all fixed.

This progression from obvious to subtle issues illustrates why single-pass auditing is insufficient for blockchain systems. New features consistently introduce new attack surfaces that require dedicated audit scope.

---

## 9. Conclusion

QBit Network demonstrates that a fully post-quantum blockchain system is practical with current NIST-standardized algorithms. The key findings are:

1. **ML-DSA-65 and ML-KEM-768 are performant**: Sub-millisecond operations enable real-time blockchain transaction processing without specialized hardware.
2. **The 55x size overhead is manageable**: For document notarization workloads with moderate transaction volumes, storage requirements remain practical.
3. **Security requires depth**: 190+ vulnerabilities across 15 audit rounds underscore the complexity of building secure blockchain systems, even with well-studied cryptographic primitives.
4. **Dual-keypair identity enables new capabilities**: The combination of ML-DSA signing with ML-KEM key encapsulation provides a natural framework for authenticated encrypted communication on-chain.
5. **ML-DSA enables P2P authentication**: The same signing primitive used for transactions and blocks can be applied directly to P2P handshake authentication, enabling post-quantum-secure node identity without additional key material.

### Current Status (v0.4.0, 2026-03-25)

- 10 transaction types (NOTARIZE, STORE, SHARE, REGISTER_KEY, REGISTER_VALIDATOR, REVOKE_KEY, STAKE, DELEGATE, UNSTAKE, EVIDENCE)
- Delegated Proof of Stake with epoch rotation and double-sign slashing
- ML-KEM-768 encrypted P2P channels with AES-256-GCM transport
- 3-step ML-DSA-65 P2P mutual authentication with verify-before-sign (SPRINT1-003 resolved)
- Genesis validator registered via on-chain REGISTER_VALIDATOR tx (SPRINT1-007 resolved)
- SQLite-primary persistence with staking, epoch, and slashing tables
- 36-endpoint REST API gateway + WebSocket subscriptions + web dashboard
- SecureBytes ctypes-backed key material zeroing (ISS-001 resolved)
- TLS auto-provisioning with renewal and hot-reload (ISS-016 resolved)
- Peer reputation scoring, chain pruning, IPFS integration, proof block signature verification
- 15 audit rounds, 190+ issues found; 1080 tests passing; 0 open issues

### Future Work

- **Block finality**: Checkpoint mechanism for faster transaction confirmation guarantees.
- **Transaction pool persistence**: WAL-based persistence so pending transactions survive node restarts.
- **Light client protocol**: Enable Merkle proof-based verification without full chain download.
- **Cross-chain anchoring**: Periodic hash commitments to established chains for additional security guarantees.
- **Proof PDF export**: Human-readable certificate PDF for legal and compliance workflows.

---

## References

1. National Institute of Standards and Technology. "FIPS 203: Module-Lattice-Based Key-Encapsulation Mechanism Standard." August 2024.
2. National Institute of Standards and Technology. "FIPS 204: Module-Lattice-Based Digital Signature Standard." August 2024.
3. National Institute of Standards and Technology. "FIPS 205: Stateless Hash-Based Digital Signature Standard." August 2024.
4. National Institute of Standards and Technology. "SP 800-227: Recommendations for Transition to Post-Quantum Cryptography Standards." November 2024.
5. Ducas, L., et al. "CRYSTALS-Dilithium: A Lattice-Based Digital Signature Scheme." IACR Transactions on Cryptographic Hardware and Embedded Systems, 2018.
6. Avanzi, R., et al. "CRYSTALS-Kyber: A CCA-Secure Module-Lattice-Based KEM." IEEE European Symposium on Security and Privacy, 2019.
7. Shor, P. "Algorithms for Quantum Computation: Discrete Logarithms and Factoring." Proceedings 35th Annual Symposium on Foundations of Computer Science, 1994.
8. Grover, L. "A Fast Quantum Mechanical Algorithm for Database Search." Proceedings 28th Annual ACM Symposium on Theory of Computing, 1996.
9. National Institute of Standards and Technology. "SP 800-57 Part 1: Recommendation for Key Management." May 2020.
10. Open Quantum Safe Project. "liboqs: An Open-Source C Library for Quantum-Safe Cryptographic Algorithms." https://openquantumsafe.org.

---

## Appendix A: Configuration Parameters

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| ML-DSA security level | Level 3 (ML-DSA-65) | Balanced security/performance for blockchain |
| ML-KEM security level | Level 3 (ML-KEM-768) | Matches signing security level |
| Block interval | 5 seconds | Sufficient for document notarization workload |
| Max transactions/block | 200 | Bounds block validation time |
| Max payload size | 8 KB | Prevents on-chain data storage abuse |
| Max pool size | 10,000 | Memory bounded (~109 MB at peak) |
| Max block drift | 30 seconds | Tolerates NTP skew without enabling timestamp manipulation |
| Max peers | 50 | Bounds connection resources |
| RPC body limit | 1 MB | Prevents memory exhaustion via HTTP |
| RPC batch limit | 50 | Prevents CPU exhaustion via batch keygen |
| scrypt N | 16,384 (min), 1,048,576 (max) | Balances brute-force resistance with DoS prevention |

## Appendix B: API Summary

### JSON-RPC 2.0

**Public (11 methods):** `qv_blockNumber`, `qv_getBlock`, `qv_getTransaction`, `qv_pendingTxCount`, `qv_verifyDocument`, `qv_getEncryptionPk`, `qv_peerCount`, `qv_nodeInfo`, `qv_validators`, `qv_getTxsBySender`, `qv_getTxsByRecipient`

**Protected (16 methods, require Bearer token):** `qv_newWallet`, `qv_listWallets`, `qv_getWalletKeys`, `qv_registerKey`, `qv_notarize`, `qv_store`, `qv_share`, `qv_getSharedWithMe`, `qv_getSharedSecret`, `qv_decapsulateShared`, `qv_sendRawTransaction`, `qv_registerValidator`, `qv_revokeKey`, `qv_stake`, `qv_delegate`, `qv_unstake`

### REST API (`/api/v1/`)

36 endpoints: 14 public (13 GET + 1 POST `/verify`) and 22 protected endpoints covering staking, epochs, slashing, wallets, and transactions. See `docs/PROTOCOL.md` Section 6 for the full endpoint list.

### WebSocket (`/ws`)

Channels: `new_block`, `new_tx`, `chain_stats`. Protocol: JSON subscribe/unsubscribe/ping/pong. See `docs/PROTOCOL.md` Section 5 for the full message format.

## Appendix C: Test Execution Summary

```
1080 tests collected
1080 passed
0 failed
0 skipped

Coverage: crypto (30+), wallet (19+), transaction (27+), block (14+),
          blockchain (40+), adversarial (46), integration (29),
          dPoS (58), REST API (47), WebSocket (34), IPFS (35),
          TLS manager (34), SecureBytes (42), reputation (26),
          pruning (10), proof signature (10)
```
