# QBit Network — v0.5.0 Release Plan

**Date:** 2026-03-25
**Compiled from:** Tech Lead, Protocol Designer (QIP-1), Security Auditor
**Timeline:** 8 weeks (4 sprints x 2 weeks)

---

## Theme

**"Financial Layer & Token Economics"**

> QBit Network introduces the native QBIT token with account-based balances, transfer and mint transactions, fixed fee schedules with burn mechanics, block rewards with halving, and epoch-based staking reward distribution — activated via hard fork.

---

## v0.5.0 Scope (14 features)

### MUST-HAVE (Sprint 1-2)

| # | Feature | Owner | Effort | Resolves |
|---|---------|-------|--------|----------|
| 1 | **Unified balance ledger** — `_debit()`/`_credit()` primitives, account-based model | blockchain-dev | L | SEC-CRIT-01 |
| 2 | **TRANSFER tx type** — signed value transfer between accounts | protocol-designer + blockchain-dev | L | QIP-1 |
| 3 | **MINT tx type** — system-level implicit block reward (not user-submitted) | protocol-designer + blockchain-dev | L | SEC-CRIT-02, QIP-1 |
| 4 | **Fee schedule** — fixed fees per tx type, 50% burned / 50% to validator | protocol-designer + blockchain-dev | M | QIP-1 |
| 5 | **Sequential intra-block balance validation** — ordered tx processing, no snapshot-based | blockchain-dev | M | SEC-CRIT-03 |
| 6 | **Supply cap enforcement** — `_total_minted` tracking, 1B QBIT hard cap | blockchain-dev | M | SEC-HIGH-01 |
| 7 | **Fee validation in `consensus.validate_block()`** | blockchain-dev | M | SEC-HIGH-04 |

### SHOULD-HAVE (Sprint 2-3)

| # | Feature | Owner | Effort | Resolves |
|---|---------|-------|--------|----------|
| 8 | **Staking migration** — unbonding redenomination to QBIT balances | protocol-designer + blockchain-dev | L | SEC-HIGH-03 |
| 9 | **Block reward halving** — 5 QBIT initial, halving every 2.1M blocks | blockchain-dev | S | QIP-1 |
| 10 | **Epoch reward distribution** — proportional delegation rewards | protocol-designer + blockchain-dev | L | QIP-1 |
| 11 | **Hard fork activation** — mempool flush at activation height, chain upgrade | blockchain-dev + devops | M | SEC-HIGH-02 |

### NICE-TO-HAVE (Sprint 3-4)

| # | Feature | Owner | Effort | Resolves |
|---|---------|-------|--------|----------|
| 12 | **RPC endpoints** — balance queries, transfer submission, fee estimation | blockchain-dev | M | — |
| 13 | **CLI wallet** — send/receive, balance check, tx history | frontend-dev | M | — |
| 14 | **NextJS wallet + block explorer updates** — token balances, transfer history, supply dashboard | frontend-dev | L | — |

---

## CUT from v0.5.0 -> v0.6.0+

| Feature | Reason |
|---------|--------|
| Multi-asset tokens (ERC-20 equivalent) | QBIT native token must stabilize first. |
| DEX / swap protocol | Requires multi-asset support. |
| Cross-chain bridge | Research phase. Needs stable financial layer. |
| Light client SPV proofs for balances | Requires Merkle Patricia trie — out of scope. |
| Governance voting with token weight | Deferred until token distribution is live. |
| ACME/Let's Encrypt (ISS-016) | Carried from v0.4.0. UX polish only. |

---

## New TX Types

| Type | Purpose | Fee | Submitter |
|------|---------|-----|-----------|
| `TRANSFER` | Move QBIT between accounts | Fixed per-tx fee | User-signed |
| `MINT` | Block reward issuance | None (system-level) | Implicit by validator (not user-submitted) |

---

## QIP-1 Configuration Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `TOKEN_NAME` | `"QBIT"` | Native token name |
| `TOKEN_DECIMALS` | `8` | Smallest unit = 1e-8 QBIT |
| `MAX_SUPPLY` | `1_000_000_000_00000000` | 1 billion QBIT (in base units) |
| `INITIAL_BLOCK_REWARD` | `5_00000000` | 5 QBIT per block (in base units) |
| `HALVING_INTERVAL` | `2_100_000` | Blocks between halvings |
| `FEE_BURN_RATIO` | `50` | Percent of fee burned |
| `FEE_VALIDATOR_RATIO` | `50` | Percent of fee to block producer |
| `ACTIVATION_HEIGHT` | TBD | Hard fork activation block height |

---

## Fee Schedule (Fixed Per TX Type)

| TX Type | Fee (QBIT) | Fee (base units) |
|---------|-----------|-------------------|
| `TRANSFER` | 0.001 | `100_000` |
| `NOTARIZE` | 0.005 | `500_000` |
| `STORE` | 0.010 | `1_000_000` |
| `SHARE` | 0.002 | `200_000` |
| `REGISTER_KEY` | 0.001 | `100_000` |
| `STAKE` | 0.001 | `100_000` |
| `DELEGATE` | 0.001 | `100_000` |
| `UNSTAKE` | 0.001 | `100_000` |
| `EVIDENCE` | 0.000 | `0` |
| `MINT` | N/A | N/A (system-level, no fee) |

---

## Dependency Graph

```
Unified balance ledger (_debit/_credit) ──── foundation for everything
        │
        ├── TRANSFER tx type ────────────── depends on balance ledger
        │
        ├── Fee schedule ────────────────── depends on balance ledger
        │       │
        │       └── Fee validation in consensus ── depends on fee schedule
        │
        ├── Supply cap (_total_minted) ──── depends on balance ledger
        │       │
        │       └── MINT tx type ────────── depends on supply cap + ledger
        │               │
        │               └── Block reward halving ── depends on MINT
        │
        └── Sequential intra-block validation ── depends on balance ledger
                │
Staking migration (redenomination) ──────── depends on balance ledger + v0.4.0 staking
        │
Epoch reward distribution ───────────────── depends on staking migration + MINT
        │
Hard fork activation ────────────────────── depends on all core features complete
        │
Mempool flush at activation height ──────── part of hard fork activation
        │
RPC endpoints ───────────────────────────── depends on balance ledger + TRANSFER
CLI wallet ──────────────────────────────── depends on RPC endpoints
NextJS wallet + explorer ────────────────── depends on RPC endpoints
```

---

## Sprint Plan

### Sprint 1 (Week 1-2): Core Balance Ledger + TRANSFER + MINT + Fees

| Task | Agent | Days |
|------|-------|------|
| Design and implement unified balance ledger with `_debit()`/`_credit()` primitives | blockchain-dev | 3 |
| Implement integer-only arithmetic (no floats), 8-decimal base unit representation | blockchain-dev | 1 |
| TRANSFER tx type: factory classmethod, validation, consensus rules | protocol-designer + blockchain-dev | 3 |
| MINT tx type: system-level implicit issuance in block production | protocol-designer + blockchain-dev | 2 |
| Supply cap enforcement via `_total_minted` counter | blockchain-dev | 1 |
| Fixed fee schedule: deduction at tx execution, 50/50 burn/validator split | blockchain-dev | 2 |
| Sequential intra-block balance validation (ordered processing) | blockchain-dev | 2 |
| Fee validation in `consensus.validate_block()` | blockchain-dev | 1 |
| Security audit of balance ledger, TRANSFER, MINT, fee logic | security-auditor | 2 |

### Sprint 2 (Week 3-4): Staking Migration + Epoch Rewards + Supply Tracking

| Task | Agent | Days |
|------|-------|------|
| Staking migration: redenominate existing stakes to QBIT balances | protocol-designer + blockchain-dev | 3 |
| Unbonding redenomination: convert in-flight unstaking to QBIT amounts | blockchain-dev | 2 |
| Block reward halving logic (5 QBIT initial, halve every 2.1M blocks) | blockchain-dev | 1 |
| Epoch reward distribution to delegators (proportional to delegation weight) | protocol-designer + blockchain-dev | 4 |
| Hard fork activation mechanism: height-gated rule switch | blockchain-dev + devops | 2 |
| Mempool flush at activation height (discard pre-fork txs) | blockchain-dev | 1 |
| QIP-1 config constants module | blockchain-dev | 1 |
| Security audit of staking migration, rewards, hard fork activation | security-auditor | 2 |

### Sprint 3 (Week 5-6): API + CLI + NextJS Wallet + Explorer Updates

| Task | Agent | Days |
|------|-------|------|
| RPC: `qbit_getBalance`, `qbit_transfer`, `qbit_estimateFee`, `qbit_getSupply` | blockchain-dev | 3 |
| RPC: `qbit_getRewards`, `qbit_getTransferHistory` | blockchain-dev | 2 |
| CLI wallet: send, receive, balance, history, fee estimation | frontend-dev | 4 |
| NextJS wallet page: balances, transfer form, tx confirmation | frontend-dev | 3 |
| Block explorer: supply dashboard, reward tracking, fee burn chart | frontend-dev | 3 |
| Integration tests: end-to-end transfer flow via RPC | test-runner | 2 |
| Documentation: QIP-1 spec, API reference, wallet guide | docs-writer | 2 |

### Sprint 4 (Week 7-8): Adversarial Tests + Security Audit + Hardening + Docs

| Task | Agent | Days |
|------|-------|------|
| Adversarial test suite (18 scenarios, see below) | test-runner + security-auditor | 5 |
| Full regression suite update (target 1200+ tests) | test-runner | 2 |
| Round 16 security audit: financial layer focus | security-auditor | 3 |
| Hardening: fix all CRITICAL/HIGH findings from audit | blockchain-dev | 3 |
| Performance benchmarks: transfer throughput, block validation with fees | perf-engineer | 2 |
| Final documentation update (FEATURES.md, CHANGELOG.md, architecture docs) | docs-writer | 2 |
| Hard fork activation height selection + testnet dry run | tech-lead + devops | 2 |
| Release candidate + final review | tech-lead | 1 |

---

## Security Auditor — 18 Required Test Scenarios

| # | Category | Scenario | Severity |
|---|----------|----------|----------|
| 1 | Balance | Transfer exact balance (zero remaining) succeeds | CRITICAL |
| 2 | Balance | Transfer more than balance fails with insufficient funds | CRITICAL |
| 3 | Balance | Double-spend: same UTXO in two txs within one block — second rejected | CRITICAL |
| 4 | Balance | Negative transfer amount rejected | CRITICAL |
| 5 | Balance | Zero transfer amount rejected | HIGH |
| 6 | Balance | Integer overflow at MAX_SUPPLY boundary — mint rejected | CRITICAL |
| 7 | Supply | `_total_minted` exceeds MAX_SUPPLY — block rejected | CRITICAL |
| 8 | Supply | Block reward after final halving is zero — no inflation | HIGH |
| 9 | MINT | User-submitted MINT tx rejected (system-only) | CRITICAL |
| 10 | MINT | MINT with incorrect reward amount rejected | CRITICAL |
| 11 | MINT | MINT to non-validator address rejected | HIGH |
| 12 | Fee | Tx with insufficient balance for fee rejected | CRITICAL |
| 13 | Fee | Fee burn amount verified (exactly 50% destroyed) | HIGH |
| 14 | Fee | Fee validator amount verified (exactly 50% credited) | HIGH |
| 15 | Fee | Block with missing/incorrect fee validation rejected by peers | HIGH |
| 16 | Fork | Pre-activation-height blocks follow old rules | HIGH |
| 17 | Fork | Post-activation-height blocks require new rules | HIGH |
| 18 | Fork | Mempool flushed at activation height — stale txs discarded | HIGH |

---

## Security Auditor Findings — Resolution Map

| Finding ID | Severity | Description | Sprint | Resolution |
|------------|----------|-------------|--------|------------|
| SEC-CRIT-01 | CRITICAL | Unified balance ledger with `_debit()`/`_credit()` primitives | 1 | Core ledger implementation |
| SEC-CRIT-02 | CRITICAL | MINT must be system-level implicit (not user-submitted tx) | 1 | MINT tx as validator-only implicit |
| SEC-CRIT-03 | CRITICAL | Sequential intra-block balance validation (no snapshot-based) | 1 | Ordered tx processing in block execution |
| SEC-HIGH-01 | HIGH | Supply cap enforcement via `_total_minted` tracking | 1 | Counter checked before every MINT |
| SEC-HIGH-02 | HIGH | Mempool flush at activation height | 2 | Flush logic in hard fork activation |
| SEC-HIGH-03 | HIGH | Unbonding redenomination during migration | 2 | Convert in-flight unstaking amounts |
| SEC-HIGH-04 | HIGH | Fee validation in `consensus.validate_block()` | 1 | Fee checks added to block validation |

**v0.5.0 resolves: 3 CRITICAL + 4 HIGH = 7 security findings**

---

## Open Issues Resolution Plan

| Issue | v0.5.0? | Action |
|-------|---------|--------|
| ISS-016 (TLS/ACME UX) | No -> v0.6.0 | Still UX polish. No security gap. |

**Remaining after v0.5.0: ISS-016 (1 issue)**

---

## Success Criteria

1. QBIT balance ledger operational: `_debit()`/`_credit()` handle all state transitions with integer arithmetic only
2. TRANSFER tx moves value between accounts; sender balance decremented, receiver balance incremented, atomically
3. MINT tx is system-level only: user-submitted MINT rejected at mempool, at consensus, and at P2P relay
4. Supply cap enforced: no block accepted that would push `_total_minted` above 1,000,000,000.00000000 QBIT
5. Block reward starts at 5 QBIT, correctly halves at each 2.1M block interval, reaches zero at exhaustion
6. Fee schedule applied to all tx types: 50% burned (provably removed from supply), 50% credited to validator
7. Sequential intra-block validation: second tx in a block sees state changes from first tx (no stale reads)
8. Hard fork activates cleanly at configured height: mempool flushed, new rules enforced, old blocks still valid before height
9. Existing v0.4.0 stakes redenominated to QBIT balances without loss of delegation or unbonding state
10. Epoch reward distribution pays delegators proportionally — no rounding exploits exceed 1 base unit
11. All 18 adversarial test scenarios pass
12. All 7 security findings resolved (3 CRITICAL, 4 HIGH)
13. 1200+ tests passing, including financial layer adversarial scenarios
14. Round 16 security audit: 0 CRITICAL, 0 HIGH

---

## Product Owner's v0.5.0 Press Release

> **"QBit Network v0.5.0: Native QBIT Token with Built-in Deflation, Fair Rewards, and Post-Quantum Security"**

Demo at conference:
1. Transfer 100 QBIT between two wallets — show real-time balance update on dashboard
2. Submit a block — show 5 QBIT minted to validator, fee split visible (burn + validator credit)
3. Show supply dashboard — total minted, total burned, circulating supply updating live
4. Attempt to submit a user-crafted MINT tx — rejected at every layer (mempool, consensus, P2P)
5. Cross the halving boundary on testnet — block reward drops from 5 to 2.5 QBIT automatically
6. Epoch boundary — delegator rewards distributed proportionally, visible in CLI wallet history
