# QBit Network -- v0.7.0 Release Plan

**Date:** 2026-03-26
**Approved by:** Product Owner / CEO
**Timeline:** 8 weeks (4 sprints x 2 weeks)
**Status:** APPROVED -- ready for execution

---

## Theme

**"Developer Foundation"**

> QBit Network v0.7.0 delivers the infrastructure developers need to build on quantum-resistant trust: state proofs via sparse Merkle tree, a receipt and event system, simple finality, an OpenAPI spec, a Python SDK, and webhooks -- all secured by NIST FIPS 203+204 post-quantum cryptography.

**Tagline:** "Build on quantum-resistant trust -- state proofs, events, SDK, and webhooks."

---

## Strategic Context

- **Shipped:** v0.6.0 with EIP-1559 dynamic fees, 1358 tests, 17 audit rounds, 0 open issues
- **Competitive window:** 18-24 months before Ethereum/IOTA complete PQC migration
- **Gap:** QBit has strong core crypto and economics. It lacks the developer-facing features (state proofs, events, SDKs) that enable an ecosystem.
- **Goal:** After v0.7.0, a developer can `pip install qbit-sdk`, notarize a document, receive a webhook on confirmation, and verify the state proof -- in under 20 lines of code.

---

## v0.7.0 Scope (7 features)

### MUST-HAVE (Sprint 1-3)

| # | Feature | Owner | Effort | Sprint |
|---|---------|-------|--------|--------|
| 1 | **State root in block header** -- sparse Merkle tree over account state (balances, nonces, stakes, document hashes) | protocol-designer + blockchain-dev | L (4-5 weeks) | 1-2 |
| 2 | **State proof RPC** -- `qbit_getStateProof(address, blockHeight)` returns Merkle inclusion proof | blockchain-dev | M | 2 |
| 3 | **Receipt/event system** -- TX receipts with status, fee_paid, events list; event emission from all TX types | blockchain-dev | M (2-3 weeks) | 2 |
| 4 | **Simple finality rule** -- block final when 2/3 of active stake has built on it | protocol-designer + blockchain-dev | S (1-2 weeks) | 2 |
| 5 | **OpenAPI 3.1 spec** -- auto-generated from RPC/REST route definitions | devops + blockchain-dev | M (1-2 weeks) | 3 |
| 6 | **Python SDK** -- `qbit-sdk` pip package: wallet, TX submission, receipt polling, event subscription, state proof verification | frontend-dev | L (3 weeks) | 3 |
| 7 | **Webhooks** -- register callback URLs, event delivery with retry + HMAC signature | blockchain-dev | M (2 weeks) | 3 |

### CUT from v0.7.0 -> v0.8.0+

| Feature | Reason |
|---------|--------|
| TypeScript SDK | OpenAPI auto-generates a usable TS client. Native SDK ships in v0.8.0 after API surface stabilizes. |
| Multi-asset tokens | Requires state root first. Ships in v0.8.0. |
| Light client protocol | Requires state root first. Ships in v0.8.0. |
| Binary P2P (msgpack) | Not a developer-facing bottleneck. Ships in v0.8.0. |
| W3C DID method | Ships in v0.8.0 (enterprise phase). |

---

## New RPC Methods

| Method | Auth | Description |
|--------|------|-------------|
| `qbit_getTransactionReceipt(tx_id)` | No | Returns receipt: status, fee_paid, events, state_root_after |
| `qbit_getLogs(filter)` | No | Query events by type, address, block range |
| `qbit_getBlockFinality(block_hash)` | No | Returns finality status (finalized / not_finalized / unknown) |
| `qbit_getStateProof(address, block_height)` | No | Returns Merkle inclusion proof for account state |
| `qbit_verifyStateProof(proof, state_root)` | No | Server-side proof verification (also available in SDK) |
| `qbit_registerWebhook(url, events, secret)` | Yes | Register a webhook endpoint |
| `qbit_listWebhooks()` | Yes | List registered webhooks |
| `qbit_deleteWebhook(webhook_id)` | Yes | Remove a webhook |

---

## State Tree Design Requirements

The sparse Merkle tree MUST cover the following state:

| Key | Value | Purpose |
|-----|-------|---------|
| `account:{address}:balance` | Integer (base units) | Balance proofs |
| `account:{address}:nonce` | Integer | Replay protection proofs |
| `account:{address}:staking` | JSON (stake amount, delegations) | Staking state proofs |
| `document:{hash}` | JSON (block_height, tx_id, timestamp) | Document notarization proofs |

**Requirements:**
- Tree must support efficient incremental updates (only recompute affected branches)
- Proof size must be O(log n) where n is the number of accounts
- Empty accounts must not bloat the tree (sparse representation)
- Tree implementation must be deterministic: same state produces same root regardless of insertion order
- Root must be computed AFTER all TXs in the block are applied (post-state root)

---

## Receipt/Event Schema

### Receipt

```json
{
  "tx_id": "abc123...",
  "block_height": 12345,
  "block_hash": "def456...",
  "status": "confirmed",
  "fee_paid": 100000,
  "state_root_after": "789abc...",
  "events": [
    {
      "type": "TRANSFER_COMPLETED",
      "data": {
        "from": "addr1...",
        "to": "addr2...",
        "amount": 500000000
      }
    }
  ]
}
```

### Event Types

| Event | Emitted By | Data |
|-------|-----------|------|
| `TRANSFER_COMPLETED` | TRANSFER TX | from, to, amount |
| `NOTARIZE_CONFIRMED` | NOTARIZE TX | address, document_hash, block_height |
| `STORE_CONFIRMED` | STORE TX | address, document_hash, encrypted |
| `SHARE_GRANTED` | SHARE TX | from, to, document_hash |
| `STAKE_DEPOSITED` | STAKE TX | address, amount |
| `STAKE_DELEGATED` | DELEGATE TX | from, validator, amount |
| `UNSTAKE_INITIATED` | UNSTAKE TX | address, amount, maturity_height |
| `VALIDATOR_REGISTERED` | REGISTER_VALIDATOR TX | address, public_key |
| `KEY_REGISTERED` | REGISTER_KEY TX | address, key_type |
| `KEY_REVOKED` | REVOKE_KEY TX | address, key_id |
| `BLOCK_FINALIZED` | Finality rule | block_hash, block_height |
| `EPOCH_REWARDS_DISTRIBUTED` | Epoch boundary | epoch, total_distributed |

---

## Webhook Delivery Spec

- **Delivery:** HTTP POST to registered URL
- **Payload:** JSON with event type, data, block_height, timestamp
- **Authentication:** HMAC-SHA256 signature in `X-QBit-Signature` header using shared secret
- **Retry policy:** 3 attempts with exponential backoff (1s, 5s, 25s)
- **Timeout:** 5 second connection timeout, 10 second read timeout
- **Failure:** After 3 failures, webhook marked as `failing`; after 10 consecutive failures, webhook disabled

---

## Dependency Graph

```
Sparse Merkle tree library ──────────────── foundation (Sprint 1)
        |
        +-- State root in block header ──── depends on tree (Sprint 1)
        |       |
        |       +-- Consensus validates state root ── depends on header (Sprint 1)
        |       |
        |       +-- State proof RPC ──────── depends on tree + header (Sprint 2)
        |
Receipt/event system ────────────────────── independent of tree (Sprint 2)
        |
        +-- qbit_getLogs RPC ────────────── depends on receipts (Sprint 2)
        |
        +-- Webhooks ────────────────────── depends on events (Sprint 3)
        |
Simple finality rule ────────────────────── independent (Sprint 2)
        |
OpenAPI spec ────────────────────────────── depends on all RPC methods (Sprint 3)
        |
Python SDK ──────────────────────────────── depends on OpenAPI + receipts + proofs (Sprint 3)
```

---

## Sprint Plan

### Sprint 1 (Weeks 1-2): State Root Foundation

| Task | Agent | Days |
|------|-------|------|
| Design sparse Merkle tree: key schema, hash function, sparse representation | protocol-designer | 2 |
| Implement sparse Merkle tree library with insert/update/proof/verify | blockchain-dev | 5 |
| Integrate state tree into blockchain: update tree on each TX execution | blockchain-dev | 2 |
| Add state_root field to block header; include in block hash computation | blockchain-dev | 2 |
| Update consensus to validate state_root in received blocks | blockchain-dev | 2 |
| Security audit: Merkle tree correctness, state root computation | security-auditor | 2 |

**Exit criteria:** Every block contains a verifiable state root. Peers reject blocks with incorrect state roots.

### Sprint 2 (Weeks 3-4): Receipts + Events + Finality + State Proofs

| Task | Agent | Days |
|------|-------|------|
| Receipt data structure and SQLite storage | blockchain-dev | 2 |
| Event emission from all TX type handlers | blockchain-dev | 3 |
| RPC: `qbit_getTransactionReceipt`, `qbit_getLogs` with filters | blockchain-dev | 2 |
| `qbit_getStateProof(address, blockHeight)` RPC endpoint | blockchain-dev | 2 |
| `qbit_verifyStateProof(proof, stateRoot)` RPC endpoint | blockchain-dev | 1 |
| Simple finality rule: 2/3 active stake confirmation tracking | protocol-designer + blockchain-dev | 3 |
| RPC: `qbit_getBlockFinality` | blockchain-dev | 1 |
| Security audit: receipt integrity, event completeness, finality logic, proof verification | security-auditor | 2 |

**Exit criteria:** Every confirmed TX has a receipt with events. Developers can query logs. Blocks report finality. State proofs are available via RPC.

### Sprint 3 (Weeks 5-6): OpenAPI + Python SDK + Webhooks

| Task | Agent | Days |
|------|-------|------|
| OpenAPI 3.1 spec generation from RPC/REST definitions | devops + blockchain-dev | 3 |
| Python SDK: wallet management, TX submission, receipt polling | frontend-dev | 3 |
| Python SDK: event subscription, state proof verification | frontend-dev | 2 |
| Python SDK: packaging (`qbit-sdk` on PyPI), typed API, docstrings | frontend-dev | 2 |
| Webhook system: registration, event delivery, HMAC signing, retry logic | blockchain-dev | 4 |
| RPC: `qbit_registerWebhook`, `qbit_listWebhooks`, `qbit_deleteWebhook` | blockchain-dev | 1 |
| Documentation: SDK quickstart, webhook integration guide, API reference | docs-writer | 2 |

**Exit criteria:** `pip install qbit-sdk` works. A developer can notarize a document and receive a webhook in under 20 lines of code. OpenAPI spec generates valid clients for TS/Go/Rust.

### Sprint 4 (Weeks 7-8): Tests + Audit + Hardening + Release

| Task | Agent | Days |
|------|-------|------|
| Adversarial tests: forged state roots, partial proofs, empty state, proof for non-existent account | test-runner + security-auditor | 3 |
| Adversarial tests: missing events, duplicate events, filtered log queries at boundaries | test-runner | 2 |
| Adversarial tests: webhook delivery failure, retry exhaustion, HMAC tampering, replay attacks | test-runner | 2 |
| SDK integration tests: end-to-end notarize + verify + webhook flow | test-runner | 2 |
| Finality adversarial tests: stake changes during finality window, validator set rotation | test-runner | 1 |
| Full regression suite (target: 1500+ tests) | test-runner | 2 |
| Round 18 security audit: state root + receipts + webhooks + SDK | security-auditor | 3 |
| Fix all CRITICAL/HIGH findings | blockchain-dev | 3 |
| Performance benchmarks: Merkle tree computation overhead per block | perf-engineer | 2 |
| Release candidate + CHANGELOG + FEATURES update | tech-lead + docs-writer | 2 |

**Exit criteria:** 0 CRITICAL, 0 HIGH from audit. 1500+ tests passing. SDK published. All docs current.

---

## Security Auditor -- Required Test Scenarios

### State Root (8 scenarios)

| # | Scenario | Severity |
|---|----------|----------|
| 1 | Block with incorrect state_root rejected by consensus | CRITICAL |
| 2 | State root deterministic: same TXs in same order produce same root | CRITICAL |
| 3 | Merkle proof verifies correctly for existing account | CRITICAL |
| 4 | Merkle proof fails for non-existent account (exclusion proof) | HIGH |
| 5 | State root updates correctly after TRANSFER (both sender and receiver) | CRITICAL |
| 6 | State root includes staking state changes (STAKE/DELEGATE/UNSTAKE) | HIGH |
| 7 | State root includes document hashes (NOTARIZE) | HIGH |
| 8 | Empty block has same state root as parent (no state change) | HIGH |

### Receipts/Events (6 scenarios)

| # | Scenario | Severity |
|---|----------|----------|
| 9 | Every confirmed TX produces exactly one receipt | CRITICAL |
| 10 | Receipt contains correct fee_paid matching actual deduction | HIGH |
| 11 | All TX types emit their corresponding events | HIGH |
| 12 | `qbit_getLogs` filter by event type returns only matching events | HIGH |
| 13 | `qbit_getLogs` filter by block range is inclusive and correct at boundaries | MEDIUM |
| 14 | Receipt not created for rejected TX (e.g., insufficient balance) | HIGH |

### Finality (4 scenarios)

| # | Scenario | Severity |
|---|----------|----------|
| 15 | Block becomes final when 2/3 of active stake has built on it | CRITICAL |
| 16 | Finality status is not retroactively lost after additional blocks | HIGH |
| 17 | Validator set change mid-finality window handled correctly | HIGH |
| 18 | Genesis block is always finalized | MEDIUM |

### Webhooks (6 scenarios)

| # | Scenario | Severity |
|---|----------|----------|
| 19 | Webhook delivered with valid HMAC-SHA256 signature | CRITICAL |
| 20 | Webhook with tampered HMAC rejected by receiver | HIGH |
| 21 | Failed delivery retries 3 times with exponential backoff | HIGH |
| 22 | Webhook disabled after 10 consecutive failures | HIGH |
| 23 | Webhook registration requires authentication | CRITICAL |
| 24 | Deleted webhook stops receiving events immediately | HIGH |

**Total: 24 adversarial scenarios (7 CRITICAL, 14 HIGH, 3 MEDIUM)**

---

## Success Criteria

1. Every block header contains a state_root computed from the sparse Merkle tree of all account state
2. Peers reject any block whose state_root does not match locally computed state after applying all TXs
3. `qbit_getStateProof` returns a verifiable Merkle proof for any account at any historical block height
4. Every confirmed TX produces a receipt with status, fee_paid, events list, and state_root_after
5. All 12 event types emitted correctly from their respective TX handlers
6. `qbit_getLogs` supports filtering by event type, address, and block range
7. Blocks report finality status: final when 2/3 of active stake has built on them
8. OpenAPI 3.1 spec validates and generates working clients for Python, TypeScript, Go
9. `pip install qbit-sdk` provides a typed Python API for wallet, TX, receipt, event, and proof operations
10. Webhooks deliver events with HMAC-SHA256 signatures; retry on failure; disable on persistent failure
11. All 24 adversarial test scenarios pass
12. Round 18 security audit: 0 CRITICAL, 0 HIGH
13. 1500+ tests passing
14. A developer can notarize a document via SDK, receive a webhook, and verify the state proof -- end to end

---

## Phase 2 Preview (v0.8.0 -- "Enterprise & Ecosystem")

| Feature | Effort | Depends On |
|---------|--------|------------|
| Multi-asset tokens (ISSUE/MINT_TOKEN/TRANSFER_TOKEN) | 3-4 weeks | v0.7.0 state root |
| Light client protocol | 3-4 weeks | v0.7.0 state proofs |
| TypeScript SDK (native) | 4-6 weeks | v0.7.0 API stability |
| Binary P2P (msgpack) | 2-3 weeks | Independent |
| W3C DID method (did:qbit) | 3-4 weeks | Independent |
| PostgreSQL sync bridge | 3-4 weeks | v0.7.0 receipts/events |

---

## Product Owner's v0.7.0 Press Release

> **"QBit Network v0.7.0 'Developer Foundation': Build on Quantum-Resistant Trust"**

Demo at conference:
1. `pip install qbit-sdk` -- show a developer notarizing a document in 5 lines of Python
2. Show the receipt with events: NOTARIZE_CONFIRMED with document hash and block height
3. Verify the state proof: prove the document exists at block N without downloading the chain
4. Register a webhook -- submit another document -- webhook fires within seconds with HMAC signature
5. Show finality: block transitions from "pending" to "finalized" as validators build on it
6. Open the OpenAPI spec in Swagger UI -- show auto-generated TypeScript client making a transfer
