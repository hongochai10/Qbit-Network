# QBit Network — Release Meeting: v0.2.0 Final Review

**Date:** 2026-03-25
**Attendees:** tech-lead, security-auditor, product-owner, blockchain-dev, protocol-designer, test-runner
**Agenda:** GO/NO-GO for v0.2.0, SQLiteStore integration, v0.2.1 planning

---

## BLOCKER: SQLiteStore Not Integrated

**Identified by:** Tech Lead, Security Auditor, Test Runner (3/6 agents independently)

SQLiteStore (209 lines, 5 tests passing) exists as standalone code but Blockchain still uses `self.chain: list[Block]` + 13 in-memory dicts + `chain.json`. ISS-006 marked "closed" but functionally incomplete.

**Tech Lead recommendation: Option C — Dual-write for v0.2.0**
- `_append_block` writes to both in-memory + SQLite
- `load()` reads from SQLite on restart
- `save()` becomes no-op (SQLite WAL commits per-block)
- Keep in-memory indices for query/consensus speed
- 1-2 days of work, 110 lines net

**Blockchain Dev integration plan:**
- Parallel architecture: SQLite = durable layer, in-memory = query layer
- 5 Blockchain methods change: `__init__`, `_append_block`, `_rollback_to`, `save`, `load`
- Add `delete_blocks_from(index)` to SQLiteStore for rollback
- Hot indices stay in-memory: `_sender_nonce`, `_pool_ids`, `_chain_tx_ids`, `_block_by_hash`

---

## Security Auditor: CONDITIONAL GO

- 0 new CRITICAL issues
- 1 HIGH: `_rollback_to` key_history `remove()` picks wrong occurrence on duplicate key registration — acceptable for single-validator MVP
- Must-do before release: update version string `0.1.0` → `0.2.0` in node.py, document proof sig gap

---

## Product Owner: User Journey Analysis

Legal professional user journey:
- Steps 1-6 (install → wallet → notarize → verify → proof → verify-proof): **WORKS**
- Step 7 (store/share via CLI): **NOT AVAILABLE** — RPC only
- Step 8 (court-admissible proof): **MISSING** — JSON only, no PDF/HTML certificate

**Verdict:** Notarize workflow complete for v0.2.0 MVP. Store/share CLI deferred to v0.2.1.

**Most valuable next thing:** Public testnet OR human-readable proof certificate

---

## Protocol Designer: v0.2.1 Proposals

1. **Drop authority scoring** — redundant with strict round-robin, simplify to pure longest-chain (~25 lines removed)
2. **HELLO_AUTH handshake** — 3-step challenge-response using ML-DSA-65, ~120 lines, protocol_version=2
3. **Protocol versioning** — `PROTOCOL_VERSION = 2` in config, negotiated via `min(initiator, responder)`

Full MSG_HELLO_AUTH wire format specified with domain-separated signatures, timestamp anti-replay, ~21.5KB handshake budget.

---

## Test Runner: Health Dashboard

```
166 tests PASSED | 4,828 lines code | 0 failures | 3.91s
SQLiteStore: present but NOT integrated
liboqs: version mismatch 0.15.0 vs 0.14.1 (non-fatal)
```

---

## DECISIONS

### Decision 1: Implement dual-write NOW, then release v0.2.0
Approved. Wire SQLiteStore into Blockchain as parallel persistence layer.

### Decision 2: v0.2.0 Release conditions
1. SQLiteStore dual-write integrated
2. Version string updated to 0.2.0
3. Proof sig verification gap documented
4. 166+ tests passing

### Decision 3: v0.2.1 Scope (next sprint)
1. Full SQLiteStore migration (replace in-memory chain list)
2. Drop authority scoring → pure longest-chain
3. HELLO_AUTH P2P handshake (ISS-002)
4. Human-readable proof certificate (HTML)
5. CLI store/share commands
6. Auto key registration on wallet create

---

## Action Items

| # | Action | Owner | Timeline |
|---|--------|-------|----------|
| 1 | Wire SQLiteStore dual-write into Blockchain | blockchain-dev | NOW |
| 2 | Add `delete_blocks_from()` to SQLiteStore | blockchain-dev | NOW |
| 3 | Update version string to 0.2.0 | blockchain-dev | NOW |
| 4 | Document proof sig gap in release notes | docs-writer | Before release |
| 5 | Run final test suite | test-runner | After items 1-3 |
| 6 | Tag v0.2.0 release + push | tech-lead | After item 5 |
