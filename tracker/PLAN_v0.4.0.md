# QBit Network — v0.4.0 Release Plan

**Date:** 2026-03-25
**Compiled from:** Tech Lead, Security Auditor, Product Owner, Protocol Designer
**Timeline:** 6 weeks (3 sprints × 2 weeks)

---

## Theme

**"Consensus Evolution & Hardened Security"**

> QBit Network replaces round-robin block production with delegated Proof-of-Stake, introduces automatic slashing for protocol violations, and hardens all P2P traffic with ML-KEM encrypted channels.

---

## v0.4.0 Scope (12 features — cut from 19+)

### MUST-HAVE (Sprint 1-2)

| # | Feature | Owner | Effort | Resolves |
|---|---------|-------|--------|----------|
| 1 | **dPoS consensus** — validator staking, delegation, weighted selection | protocol-designer + blockchain-dev | XL | — |
| 2 | **Epoch rotation** — validators rotate per epoch (100 blocks) | protocol-designer + blockchain-dev | L | — |
| 3 | **Slashing** — evidence txs, stake penalty, nothing-at-stake mitigation | protocol-designer + blockchain-dev | L | F-01 |
| 4 | **Auth protocol fix: verify-before-sign** | blockchain-dev | M | SPRINT1-003 |
| 5 | **Genesis validator on-chain tx** | blockchain-dev | S | SPRINT1-007 |

### SHOULD-HAVE (Sprint 2-3)

| # | Feature | Owner | Effort | Resolves |
|---|---------|-------|--------|----------|
| 6 | **P2P encrypted channel** — ML-KEM session key + AES-GCM | protocol-designer + blockchain-dev | L | — |
| 7 | **Peer reputation scoring** | blockchain-dev | M | ISS-009 |
| 8 | **Connection dedup** | blockchain-dev | S | A-01 |
| 9 | **Block signature in proof verification** | blockchain-dev | S | R14-006 |
| 10 | **Chain pruning** | devops + blockchain-dev | M | ISS-007 |

### NICE-TO-HAVE (Sprint 3)

| # | Feature | Owner | Effort | Resolves |
|---|---------|-------|--------|----------|
| 11 | **Key material zeroing** via ctypes/mmap | blockchain-dev | M | ISS-001 |
| 12 | **SPRINT1-011 closure** — verify parameterized queries | blockchain-dev | S | SPRINT1-011 |

---

## CUT from v0.4.0 → v0.5.0+

| Feature | Reason |
|---------|--------|
| ACME/Let's Encrypt (ISS-016) | TLS works. ACME is UX polish. No security gap. |
| TX pool persistence | Edge case — nodes rarely crash during block production. |
| Light client protocol | Requires stable dPoS epoch rotation first. |
| Cross-chain bridge | Research phase — no customer demand yet. |
| Proof PDF export | HTML proof is sufficient. |
| Block finality (checkpoints) | Needs epoch rotation operational before checkpoints make sense. |
| Validator staking rewards | Inflation model not finalized. Separate release. |

---

## New TX Types

| Type | Purpose |
|------|---------|
| `STAKE` | Lock coins as validator stake |
| `DELEGATE` | Delegate stake weight to a validator |
| `UNSTAKE` | Withdraw stake (unbonding period applies) |
| `EVIDENCE` | Submit double-signing or equivocation proof |

---

## Dependency Graph

```
STAKE/DELEGATE tx types ─────── required by dPoS consensus
        │
dPoS consensus ────────────────── weighted validator selection
        │
Epoch rotation ─────────────────── depends on dPoS validator set
        │
Slashing ────────────────────────── depends on epoch rotation + EVIDENCE tx
        │
Auth fix (verify-before-sign) ──── standalone, do in Sprint 1
Genesis validator tx ───────────── standalone, do in Sprint 1
        │
P2P encrypted channel ───────────── standalone, parallel in Sprint 2
Connection dedup ────────────────── prerequisite for peer reputation
Peer reputation scoring ─────────── depends on connection dedup
Block sig in proof verify ──────── standalone fix
Chain pruning ───────────────────── standalone, after SQLite stable
Key material zeroing ────────────── standalone, low risk
SPRINT1-011 closure ─────────────── standalone, verification only
```

---

## Sprint Plan

### Sprint 1 (Week 1-2): dPoS Foundation + Security Fixes

| Task | Agent | Days |
|------|-------|------|
| STAKE, DELEGATE, UNSTAKE tx types + consensus rules | protocol-designer + blockchain-dev | 4 |
| dPoS weighted validator selection (replace round-robin) | protocol-designer + blockchain-dev | 3 |
| Auth protocol fix: verify-before-sign | blockchain-dev | 2 |
| Genesis validator on-chain tx | blockchain-dev | 1 |
| Security audit of dPoS and auth changes | security-auditor | 2 |

### Sprint 2 (Week 3-4): Epoch Rotation + Slashing + P2P Encryption

| Task | Agent | Days |
|------|-------|------|
| Epoch rotation (100-block window, validator set swap) | protocol-designer + blockchain-dev | 4 |
| Slashing: EVIDENCE tx type, stake penalty, double-sign detection | protocol-designer + blockchain-dev | 4 |
| P2P encrypted channel (ML-KEM key exchange + AES-GCM framing) | blockchain-dev | 3 |
| Connection dedup | blockchain-dev | 1 |
| Security audit of slashing + P2P encryption | security-auditor | 2 |

### Sprint 3 (Week 5-6): Reputation + Pruning + Polish + Audit

| Task | Agent | Days |
|------|-------|------|
| Peer reputation scoring (auto-ban misbehaving peers) | blockchain-dev | 3 |
| Chain pruning (configurable retention depth) | devops + blockchain-dev | 3 |
| Block signature in proof verification | blockchain-dev | 1 |
| Key material zeroing via ctypes/mmap | blockchain-dev | 2 |
| SPRINT1-011 closure (parameterized query audit) | blockchain-dev | 1 |
| Dashboard updates (staking UI, epoch display, validator weights) | frontend-dev | 3 |
| Full test suite update (900+ tests target) | test-runner | 2 |
| Documentation update (all docs) | docs-writer | 2 |
| Round 15 security audit | security-auditor | 2 |
| Release candidate + final review | tech-lead | 1 |

---

## Open Issues Resolution Plan

| Issue | v0.4.0? | Action |
|-------|---------|--------|
| ISS-001 (key zeroing) | **Yes** | Sprint 3. ctypes/mmap approach. Risk 3/10. |
| ISS-007 (chain pruning) | **Yes** | Sprint 3. SQLite backend now stable. |
| ISS-009 (Sybil/Eclipse) | **Yes** | Sprint 3. Peer reputation + connection dedup. |
| ISS-016 (TLS/ACME UX) | No → v0.5.0 | TLS works. ACME is polish only. |

**v0.4.0 closes: ISS-001, ISS-007, ISS-009 (3 issues)**
**Remaining after v0.4.0: ISS-016 (1 issue)**

---

## Deferred Findings Resolution Plan

| Finding | Source | v0.4.0 Action |
|---------|--------|---------------|
| SPRINT1-003 | Sprint 1 audit | Auth protocol fix: verify-before-sign (Sprint 1) |
| SPRINT1-007 | Sprint 1 audit | Genesis validator on-chain tx (Sprint 1) |
| SPRINT1-011 | Sprint 1 audit | Verify parameterized queries (Sprint 3) |
| F-01 | Round 14 audit | Slashing + nothing-at-stake mitigation (Sprint 2) |
| A-01 | Round 14 audit | Connection dedup (Sprint 2) |
| R14-006 | Round 14 audit | Block signature in proof verification (Sprint 3) |

**v0.4.0 resolves: SPRINT1-003, SPRINT1-007, SPRINT1-011, F-01, A-01, R14-006 (6 deferred findings)**

---

## Success Criteria

1. Three validators with unequal stake produce blocks proportional to weight for 24h without divergence
2. Double-signing evidence triggers automatic slashing — penalized validator loses stake
3. Epoch rotation transitions cleanly with no forks or missed blocks
4. All P2P traffic encrypted via ML-KEM session key + AES-GCM — plaintext connections rejected
5. Auth protocol hardened: signature verification always precedes signing operations
6. Peer reputation scoring auto-bans peers that exceed violation thresholds
7. Chain pruning stabilizes disk usage under continuous operation
8. All 6 deferred audit findings resolved (SPRINT1-003, SPRINT1-007, SPRINT1-011, F-01, A-01, R14-006)
9. 900+ tests passing, including dPoS adversarial and slashing scenarios
10. Round 15 security audit: 0 CRITICAL, 0 HIGH

---

## Product Owner's v0.4.0 Press Release

> **"QBit Network v0.4.0: Delegated Proof-of-Stake, Automatic Slashing, and Fully Encrypted P2P on a Post-Quantum Blockchain"**

Demo at conference:
1. Show three validators with different stake weights — block production ratio matches stake
2. Submit a double-signing evidence transaction — watch stake slashed in real-time on dashboard
3. Epoch boundary transitions live — validator set rotates, chain continues uninterrupted
4. Wireshark capture shows all P2P traffic as opaque AES-GCM ciphertext
5. Attempt connection from unregistered peer — rejected at ML-KEM handshake
