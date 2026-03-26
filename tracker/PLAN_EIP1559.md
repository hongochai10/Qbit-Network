# QBit Network — EIP-1559 Dynamic Fees Plan

**Date:** 2026-03-26
**Compiled from:** Tech Lead, Protocol Designer, Security Auditor
**Timeline:** 6 weeks (3 sprints x 2 weeks)

---

## Theme

**"EIP-1559 Dynamic Fees + 100% Validator Reward"**

> QBit Network replaces fixed per-TX fees with a dynamic base fee that adjusts ±12.5% per block based on block weight utilization (target 50%). All fees go to the validator. Anti-spam controls prevent self-TXs from manipulating the base fee. Activated via hard fork at `DYNAMIC_FEE_ACTIVATION_HEIGHT`.

---

## Scope (11 features)

### MUST-HAVE (Sprint 1-2)

| # | Feature | Owner | Effort | Resolves |
|---|---------|-------|--------|----------|
| 1 | **fees.py** — base fee calculation engine: `compute_base_fee()`, `compute_tx_fee()`, `compute_effective_block_weight()` | blockchain-dev | L | Core design |
| 2 | **TX weight constants** — `TX_WEIGHTS` dict replacing `TX_FEES` in config.py | protocol-designer + blockchain-dev | S | Core design |
| 3 | **Block header fields** — add `base_fee` to block header; serialization + deserialization | blockchain-dev | M | Core design |
| 4 | **TX fields** — add `max_fee_per_weight` + `max_priority_fee` to transaction; validation + serialization | blockchain-dev | M | Core design |
| 5 | **Consensus validation** — validate `base_fee` derivation, TX fee sufficiency, self-TX cap (25%) in `consensus.validate_block()` | blockchain-dev | L | Anti-spam |
| 6 | **Fee deduction + validator credit** — 100% of fee credited to validator, 0% burned | blockchain-dev | M | Core design |
| 7 | **Hard fork activation** — pre-activation path uses legacy fee logic; post-activation uses dynamic fees; mempool flush at height | blockchain-dev + devops | M | Migration |

### SHOULD-HAVE (Sprint 2)

| # | Feature | Owner | Effort | Resolves |
|---|---------|-------|--------|----------|
| 8 | **TX pool admission** — reject TXs where `max_fee_per_weight < current_base_fee`; order pool by effective priority fee | blockchain-dev | M | Anti-spam |
| 9 | **Block production** — select TXs by descending priority fee; enforce `MAX_BLOCK_WEIGHT`; allow empty blocks post-activation | blockchain-dev | M | Core design |
| 10 | **Rollback support** — restore previous `base_fee` on chain reorg; re-admit pool TXs whose max_fee_per_weight now qualifies | blockchain-dev | M | Correctness |

### NICE-TO-HAVE (Sprint 3)

| # | Feature | Owner | Effort | Resolves |
|---|---------|-------|--------|----------|
| 11 | **NextJS fee dashboard** — live base fee chart, estimated fee widget, priority fee input in transfer form | frontend-dev | L | UX |

---

## CUT from this release -> future

| Feature | Reason |
|---------|--------|
| Fee market MEV protection (private mempool) | Requires encrypted mempool design. Out of scope. |
| Fee token burning (EIP-1559 original) | Design decision: 100% to validator. No burn. |
| Per-validator priority fee tipping UI | CLI only for now; NextJS widget is NICE-TO-HAVE. |
| Base fee oracle RPC for external callers | Low priority until external wallet integrations exist. |

---

## New Config Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `TX_WEIGHTS` | See table below | Weight per TX type (replaces `TX_FEES`) |
| `MAX_BLOCK_WEIGHT` | `20_000_000` | Hard cap on total block weight |
| `TARGET_BLOCK_WEIGHT` | `10_000_000` | Target 50% utilization for base fee stability |
| `BASE_FEE_CHANGE_DENOM` | `8` | Denominator for ±12.5% adjustment (1/8) |
| `INITIAL_BASE_FEE` | `10` | Base fee at activation height (base units per weight unit) |
| `MIN_BASE_FEE` | `1` | Floor — base fee never goes below this |
| `MAX_BASE_FEE` | `10_000` | Ceiling — base fee never exceeds this |
| `DYNAMIC_FEE_ACTIVATION_HEIGHT` | TBD | Hard fork height; pre-activation uses legacy fee schedule |

---

## TX Weight Table

| TX Type | Weight |
|---------|--------|
| `TRANSFER` | `1_000` |
| `NOTARIZE` | `5_000` |
| `STORE` | `10_000` |
| `SHARE` | `2_000` |
| `REGISTER_KEY` | `1_000` |
| `STAKE` | `1_000` |
| `DELEGATE` | `1_000` |
| `UNSTAKE` | `1_000` |
| `EVIDENCE` | `500` |
| `MINT` | `0` (system-level, no weight) |

---

## Fee Mechanics

### Base Fee Adjustment

```
if effective_block_weight > TARGET_BLOCK_WEIGHT:
    delta = base_fee * (effective_block_weight - TARGET_BLOCK_WEIGHT) // (TARGET_BLOCK_WEIGHT * BASE_FEE_CHANGE_DENOM)
    new_base_fee = base_fee + max(delta, 1)
elif effective_block_weight < TARGET_BLOCK_WEIGHT:
    delta = base_fee * (TARGET_BLOCK_WEIGHT - effective_block_weight) // (TARGET_BLOCK_WEIGHT * BASE_FEE_CHANGE_DENOM)
    new_base_fee = base_fee - delta
else:
    new_base_fee = base_fee

new_base_fee = clamp(new_base_fee, MIN_BASE_FEE, MAX_BASE_FEE)
```

Maximum single-block change: ±12.5% of current base fee.

### Effective Block Weight (Anti-Spam)

Self-TXs (sender == block producer) are excluded from `effective_block_weight` used in base fee calculation. They still consume physical block space and count toward `MAX_BLOCK_WEIGHT`.

Self-TX cap: self-TX weight must not exceed 25% of `MAX_BLOCK_WEIGHT` (5,000,000 weight units).

### TX Fee Calculation

```
effective_priority = min(max_priority_fee, max_fee_per_weight - base_fee)
tx_fee = (base_fee + effective_priority) * tx_weight
```

TX is valid for inclusion when `max_fee_per_weight >= base_fee`.

### Fee Distribution

100% of `tx_fee` credited to the block producer. 0% burned.

---

## Files to Modify

| File | Change |
|------|--------|
| `qbit_network/core/config.py` | Add `TX_WEIGHTS`, `MAX_BLOCK_WEIGHT`, `TARGET_BLOCK_WEIGHT`, `BASE_FEE_CHANGE_DENOM`, `INITIAL_BASE_FEE`, `MIN_BASE_FEE`, `MAX_BASE_FEE`, `DYNAMIC_FEE_ACTIVATION_HEIGHT` |
| `qbit_network/core/fees.py` | New file: `compute_base_fee()`, `compute_tx_fee()`, `compute_effective_block_weight()`, `select_txs_for_block()` |
| `qbit_network/core/block.py` | Add `base_fee` field to block header; update `to_dict()`, `from_dict()`, hash computation |
| `qbit_network/core/transaction.py` | Add `max_fee_per_weight`, `max_priority_fee` fields; update factory classmethods, `to_dict()`, `from_dict()`, validation |
| `qbit_network/core/consensus.py` | Add dynamic fee validation: base fee derivation check, TX fee sufficiency, self-TX cap |
| `qbit_network/core/blockchain.py` | Fee deduction at TX execution (100% to validator); pool admission filtering; rollback of base fee on reorg; mempool flush at activation height |
| `docs/PROTOCOL.md` | Dynamic fee wire format, base fee adjustment formula, TX field spec |
| `tests/` | New test modules for fee engine, adversarial scenarios |

---

## Dependency Graph

```
TX_WEIGHTS + new config constants ───────── foundation
        │
        ├── fees.py (compute_base_fee, compute_tx_fee, compute_effective_block_weight)
        │       │
        │       ├── block.py: base_fee field ──────── depends on fees.py
        │       │
        │       ├── transaction.py: max_fee_per_weight + max_priority_fee
        │       │
        │       └── consensus.py: base fee validation ── depends on fees.py + block.py + transaction.py
        │               │
        │               └── blockchain.py: fee deduction + pool admission ── depends on consensus.py
        │                       │
        │                       ├── block production: TX selection by priority fee
        │                       ├── rollback: restore prior base_fee
        │                       └── hard fork activation: mempool flush at height
        │
NextJS fee dashboard ────────────────────── depends on RPC + blockchain.py
```

---

## Sprint Plan

### Sprint 1 (Week 1-2): Core Fee Engine + Block/TX Fields + Consensus Validation

| Task | Agent | Days |
|------|-------|------|
| Add `TX_WEIGHTS` and all new fee constants to config.py | blockchain-dev | 1 |
| Implement fees.py: `compute_base_fee()`, `compute_tx_fee()`, `compute_effective_block_weight()` | blockchain-dev | 3 |
| Add `base_fee` field to block.py: header, `to_dict()`, `from_dict()`, hash | blockchain-dev | 2 |
| Add `max_fee_per_weight` + `max_priority_fee` to transaction.py: factory, validation, serialization | blockchain-dev | 2 |
| Update consensus.py: validate derived base_fee, per-TX fee sufficiency, self-TX cap (25%) | blockchain-dev | 3 |
| Security audit: fee engine logic, block header change, TX field validation | security-auditor | 2 |

### Sprint 2 (Week 3-4): Blockchain Integration + Migration + Pool + Block Production

| Task | Agent | Days |
|------|-------|------|
| Fee deduction in blockchain.py: 100% to validator per TX | blockchain-dev | 2 |
| TX pool admission: filter by `max_fee_per_weight >= current_base_fee`; order by priority fee | blockchain-dev | 2 |
| Block production: select TXs by descending priority fee; enforce `MAX_BLOCK_WEIGHT`; allow empty blocks | blockchain-dev | 3 |
| Rollback support: restore `base_fee` from parent block on reorg; re-admit pool TXs | blockchain-dev | 2 |
| Hard fork activation: height-gated rule switch; mempool flush at `DYNAMIC_FEE_ACTIVATION_HEIGHT` | blockchain-dev + devops | 2 |
| Legacy pre-activation path: fixed fee schedule still used below activation height | blockchain-dev | 1 |
| Security audit: fee deduction, pool admission, rollback, activation path | security-auditor | 2 |

### Sprint 3 (Week 5-6): API + NextJS Updates + Tests + Security Audit

| Task | Agent | Days |
|------|-------|------|
| RPC: `qbit_estimateFee(tx_type)` — returns current base_fee, weight, estimated total fee | blockchain-dev | 2 |
| RPC: `qbit_getBaseFee()` — current base fee and block weight utilization | blockchain-dev | 1 |
| Update `docs/PROTOCOL.md`: dynamic fee wire format, base fee adjustment spec, TX fields | docs-writer | 2 |
| NextJS fee dashboard: base fee chart, estimated fee widget, priority fee input | frontend-dev | 4 |
| Unit tests: `compute_base_fee()` at all boundary conditions | test-runner | 2 |
| Unit tests: TX fee calculation, priority fee clamping, self-TX exclusion | test-runner | 2 |
| Adversarial test suite (20 scenarios — see below) | test-runner + security-auditor | 4 |
| Full regression suite: all legacy tests pass via pre-activation path | test-runner | 2 |
| Round 18 security audit: dynamic fee focus | security-auditor | 3 |
| Hardening: fix all CRITICAL/HIGH findings from audit | blockchain-dev | 2 |
| Final documentation update (FEATURES.md, CHANGELOG.md) | docs-writer | 1 |

---

## Security Auditor — 20 Required Test Scenarios

| # | Category | Scenario | Severity |
|---|----------|----------|----------|
| 1 | Base Fee | Block at exactly `TARGET_BLOCK_WEIGHT` — base fee unchanged | CRITICAL |
| 2 | Base Fee | Block at `MAX_BLOCK_WEIGHT` — base fee increases by exactly 12.5% | CRITICAL |
| 3 | Base Fee | Empty block (0 effective weight) — base fee decreases by 12.5% | CRITICAL |
| 4 | Base Fee | Base fee never drops below `MIN_BASE_FEE` | CRITICAL |
| 5 | Base Fee | Base fee never exceeds `MAX_BASE_FEE` | HIGH |
| 6 | Base Fee | Block with wrong derived `base_fee` rejected by consensus | CRITICAL |
| 7 | Self-TX | Self-TXs excluded from `effective_block_weight` — base fee unaffected | CRITICAL |
| 8 | Self-TX | Self-TXs still counted toward `MAX_BLOCK_WEIGHT` physical cap | HIGH |
| 9 | Self-TX | Block where self-TX weight exceeds 25% of `MAX_BLOCK_WEIGHT` is rejected | CRITICAL |
| 10 | Self-TX | Validator fills block with 100% self-TXs — base fee does not increase | CRITICAL |
| 11 | TX Fee | TX with `max_fee_per_weight < base_fee` rejected at pool admission | CRITICAL |
| 12 | TX Fee | TX with `max_fee_per_weight < base_fee` rejected at consensus validation | CRITICAL |
| 13 | TX Fee | `effective_priority` clamped: `min(max_priority_fee, max_fee_per_weight - base_fee)` | HIGH |
| 14 | TX Fee | Validator receives exactly `(base_fee + effective_priority) * tx_weight` per TX | CRITICAL |
| 15 | TX Fee | 0% of fee burned — supply unchanged by fee collection | HIGH |
| 16 | Block Production | TXs selected in descending priority fee order | HIGH |
| 17 | Block Production | Block weight does not exceed `MAX_BLOCK_WEIGHT` | HIGH |
| 18 | Fork | Pre-activation blocks use legacy fixed fee schedule | HIGH |
| 19 | Fork | Post-activation blocks require dynamic fee fields; old-style TX rejected | HIGH |
| 20 | Fork | Mempool flushed at activation height — TXs lacking new fields discarded | HIGH |

---

## Security Auditor Findings — Resolution Map

| Finding ID | Severity | Description | Sprint | Resolution |
|------------|----------|-------------|--------|------------|
| DYN-CRIT-01 | CRITICAL | Self-TX manipulation of base_fee | 1+2 | Exclude self-TX from effective_block_weight; 25% self-TX cap in consensus |
| DYN-CRIT-02 | CRITICAL | Base fee derivation not validated by peers | 1 | Consensus recomputes and rejects mismatched base_fee |
| DYN-CRIT-03 | CRITICAL | TX admitted to pool with stale max_fee_per_weight | 2 | Pool re-filters on every new block |
| DYN-CRIT-04 | CRITICAL | Fee deduction missing for one or more TX types | 2 | Unified fee deduction path in blockchain.py covers all types |
| DYN-HIGH-01 | HIGH | BASE_FEE below MIN_BASE_FEE allows zero-fee spam | 1 | Clamp enforced in compute_base_fee() |
| DYN-HIGH-02 | HIGH | Rollback restores wrong base_fee after reorg | 2 | base_fee stored in block header; rollback reads from parent block |
| DYN-HIGH-03 | HIGH | Pre-activation legacy TXs accepted post-activation | 2 | Mempool flush + activation height check in consensus |

**This release resolves: 4 CRITICAL + 3 HIGH = 7 security findings**

---

## Success Criteria

1. Base fee increases when `effective_block_weight > TARGET_BLOCK_WEIGHT`, decreases when below — by exactly ±12.5% (clamped)
2. Self-TX weight excluded from base fee calculation — a validator filling a block with self-TXs cannot inflate `base_fee`
3. Self-TX weight still counts against `MAX_BLOCK_WEIGHT`; blocks exceeding 25% self-TX weight are rejected
4. All legacy (pre-activation) tests pass without modification via the pre-activation code path
5. 100% of fees credited to the block producer; 0% burned; supply accounting matches
6. Block production selects TXs by descending effective priority fee
7. TX pool rejects any TX where `max_fee_per_weight < current_base_fee`
8. Peers reject any block whose `base_fee` does not match the value derived from the parent block
9. Rollback correctly restores `base_fee` to the parent block's value on reorg
10. Hard fork activates cleanly: mempool flushed at `DYNAMIC_FEE_ACTIVATION_HEIGHT`, new TX fields required post-activation
11. All 20 adversarial test scenarios pass
12. All 7 security findings resolved (4 CRITICAL, 3 HIGH)
13. Round 18 security audit: 0 CRITICAL, 0 HIGH

---

## Product Owner's Press Release

> **"QBit Network EIP-1559: Self-Regulating Fee Market with Full Validator Rewards and Spam-Proof Design"**

Demo at conference:
1. Show base fee adjusting in real time as blocks fill and empty — fee dashboard chart updates each block
2. Submit a batch of self-TXs as validator — show that base fee does not move despite full blocks
3. Submit a TX with priority fee above base fee — confirm it is included before lower-priority TXs
4. Attempt to submit a TX with `max_fee_per_weight` below current base fee — rejected at pool admission
5. Cross the activation height on testnet — old TX format rejected; new format required automatically
6. Show validator balance increasing by exactly `sum(base_fee + priority) * weight` per block
