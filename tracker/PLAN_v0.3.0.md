# QBit Network — v0.3.0 Release Plan

**Date:** 2026-03-25
**Compiled from:** Tech Lead, Security Auditor, Product Owner, Protocol Designer
**Timeline:** 6 weeks (3 sprints × 2 weeks)

---

## Theme

**"Multi-Validator & Enterprise-Ready"**

> QBit Network becomes a production multi-validator blockchain with authenticated P2P, a web explorer, and enterprise integration capabilities.

---

## v0.3.0 Scope (10 features — cut from 31)

### MUST-HAVE (Sprint 1-2)

| # | Feature | Owner | Effort | Resolves |
|---|---------|-------|--------|----------|
| 1 | **HELLO_AUTH server-side** — complete P2P authentication | blockchain-dev | M | ISS-002 |
| 2 | **Multi-validator key distribution on-chain** | protocol-designer + blockchain-dev | L | ISS-008 |
| 3 | **Full SQLite migration** — replace in-memory chain list | blockchain-dev | L | Tech debt |
| 4 | **Rate limiting** per-peer and per-RPC-client | devops | M | DoS prevention |
| 5 | **Web dashboard / chain explorer** | frontend-dev | L | Enterprise sales |

### SHOULD-HAVE (Sprint 2-3)

| # | Feature | Owner | Effort | Resolves |
|---|---------|-------|--------|----------|
| 6 | **REST API gateway** | devops + frontend-dev | M | Enterprise integration |
| 7 | **WebSocket subscriptions** (new block, new tx) | blockchain-dev | S | Real-time events |
| 8 | **CI expansion** — adversarial + integration tests | devops | S | ISS-015 |
| 9 | **Key revocation transactions** | protocol-designer + blockchain-dev | M | ISS-010 |
| 10 | **IPFS integration** for STORE/SHARE | frontend-dev | M | CLI completeness |

---

## CUT from v0.3.0 → v0.4.0+

| Feature | Reason |
|---------|--------|
| dPoS consensus | Massive scope — consensus rewrite is a full release. Ship after P2P auth + multi-validator are stable |
| Slashing | Requires dPoS. Needs design time for evidence format + fraud proofs |
| Epoch rotation | Depends on dPoS |
| Key material zeroing (ISS-001) | C extension work, risk=3/10, defense-in-depth |
| P2P encrypted channel | HELLO_AUTH is the real gap. Encryption is defense-in-depth |
| Chain pruning (ISS-007) | Operational, risk=2/10. After full SQLite migration |
| TX pool persistence | Edge case — nodes rarely crash during block production |
| Peer reputation (ISS-009) | Rate limiting covers acute DoS. Reputation follows auth |
| Light client protocol | Needs stable multi-validator first |
| Cross-chain bridge | Research phase — no customer demand yet |
| ACME/Let's Encrypt | UX polish, not security gap |
| Proof PDF export | Already have HTML, sufficient for v0.3.0 |

---

## Dependency Graph

```
ISS-002 (P2P auth) ──────────────── standalone, do first
        │
ISS-008 (validator keys on-chain) ── enables ISS-002 full verification
        │
Full SQLite migration ────────────── enables scale for multi-validator
        │
Rate limiting ────────────────────── standalone, parallel with above
        │
Web dashboard ────────────────────── needs REST gateway
REST API gateway ─────────────────── enables dashboard + enterprise
WebSocket ────────────────────────── enables dashboard real-time
Key revocation ───────────────────── needs validator key distribution
IPFS integration ─────────────────── standalone CLI feature
CI expansion ─────────────────────── standalone infra
```

---

## Sprint Plan

### Sprint 1 (Week 1-2): Security Foundation

| Task | Agent | Days |
|------|-------|------|
| HELLO_AUTH server-side handler + verification | blockchain-dev | 3 |
| Multi-validator key distribution tx type | protocol-designer + blockchain-dev | 4 |
| Rate limiting middleware (P2P + RPC) | devops | 2 |
| CI expansion (adversarial tests in Actions) | devops | 1 |
| Security audit of P2P auth | security-auditor | 1 |

### Sprint 2 (Week 3-4): Storage + API

| Task | Agent | Days |
|------|-------|------|
| Full SQLite migration (remove chain list) | blockchain-dev | 5 |
| REST API gateway (aiohttp routes) | devops + frontend-dev | 3 |
| WebSocket subscription endpoints | blockchain-dev | 2 |
| Key revocation tx type + consensus rules | protocol-designer + blockchain-dev | 3 |
| Security audit of storage + API | security-auditor | 1 |

### Sprint 3 (Week 5-6): Enterprise Features + Polish

| Task | Agent | Days |
|------|-------|------|
| Web dashboard (chain explorer UI) | frontend-dev | 5 |
| IPFS integration for STORE/SHARE CLI | frontend-dev | 3 |
| Full test suite update (200+ tests target) | test-runner | 2 |
| Documentation update (all docs) | docs-writer | 2 |
| Round 13 security audit | security-auditor | 2 |
| Release candidate + final review | tech-lead | 1 |

---

## Open Issues Resolution Plan

| Issue | v0.3.0? | Action |
|-------|---------|--------|
| ISS-001 (key zeroing) | No → v0.4.0 | Risk 3/10. C extension work. |
| ISS-002 (P2P auth) | **Yes** | Sprint 1. Highest impact. |
| ISS-007 (pruning) | No → v0.4.0 | Risk 2/10. After full SQLite. |
| ISS-008 (validator keys) | **Yes** | Sprint 1. Unblocks ISS-002 + multi-validator. |
| ISS-009 (Sybil/Eclipse) | No → v0.4.0 | Rate limiting covers acute risk. |
| ISS-010 (key revocation) | **Yes** | Sprint 2. |
| ISS-012 (nonce naming) | **Yes** | Trivial rename during SQLite migration. |
| ISS-013 (reverse blocks) | No → WONTFIX | Self-healing on retry. |
| ISS-014 (wallet ownership) | WONTFIX | By design. Auth token sufficient. |
| ISS-015 (CI expansion) | **Yes** | Sprint 1. |
| ISS-016 (TLS UX) | No → v0.4.0 | TLS works. ACME is polish. |

**v0.3.0 closes: ISS-002, ISS-008, ISS-010, ISS-012, ISS-015 (5 issues)**
**Remaining after v0.3.0: ISS-001, ISS-007, ISS-009, ISS-016 (4 issues)**
**WONTFIX: ISS-013, ISS-014**

---

## Success Criteria

1. Two validator nodes run for 24h without divergence
2. P2P connections authenticated — unauthenticated peers rejected for block sync
3. Web dashboard shows blocks, txs, validators in real-time
4. REST API passes Postman collection with 50+ endpoints
5. 200+ tests passing, including multi-validator adversarial scenarios
6. Round 13 security audit: 0 CRITICAL, 0 HIGH

---

## Product Owner's v0.3.0 Press Release

> **"QBit Network v0.3.0: The First Multi-Validator Post-Quantum Blockchain with Authenticated P2P and Enterprise Web Interface"**

Demo at conference:
1. Show web dashboard with 3 validators producing blocks in real-time
2. Notarize a document on node 1, verify on node 2's dashboard
3. Show P2P auth rejecting an unauthenticated peer
4. Export HTML proof certificate → open in browser
5. Call REST API from Postman → enterprise integration story
