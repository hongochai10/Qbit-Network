# QBit Network PQC Blockchain — Claude Code Project Guide

## Project Overview
Post-quantum cryptography blockchain for document notarization and encrypted sharing.
- **Language:** Python 3.11+
- **PQC:** ML-DSA-65 + ML-KEM-768 via liboqs
- **Framework:** asyncio + aiohttp
- **Version:** v0.8.0 (complete) — 14 TX types, dPoS, state proofs, receipts, SDK, webhooks, multi-asset tokens, light client, binary P2P, 1781 tests, 22 audit rounds, 0 open issues

## Agents Team

### Leadership
| Agent | Role | Model | Use When |
|-------|------|-------|----------|
| `tech-lead` | **Orchestrator** — dispatches work, makes arch decisions, reviews all output | opus | Starting any non-trivial task, resolving trade-offs, release decisions |
| `product-owner` | **Vision** — defines requirements, prioritizes backlog, represents users | opus | Feature planning, acceptance criteria, roadmap decisions |

### Specialists
| Agent | Role | Model | Use When |
|-------|------|-------|----------|
| `security-auditor` | Security review and vulnerability assessment | opus | Auditing code changes, reviewing PRs, threat analysis |
| `blockchain-dev` | Core blockchain implementation | opus | Building features, fixing bugs, refactoring |
| `protocol-designer` | Protocol architecture and design decisions | opus | Consensus changes, P2P upgrades, crypto decisions |
| `researcher` | Academic research, literature review, paper writing | opus | PQC analysis, benchmarks, publications |
| `test-runner` | Comprehensive testing (unit, integration, adversarial) | sonnet | After code changes, before releases |
| `docs-writer` | Documentation and tracker maintenance | sonnet | After features/fixes, updating docs |
| `report-writer` | Audit/test/status reports | sonnet | After audits, releases, milestones |
| `devops` | Infrastructure, CI/CD, TLS, database backends | sonnet | Deployment, monitoring, DB migration |
| `frontend-dev` | CLI tools, web dashboard, chain explorer | sonnet | User-facing tools, IPFS integration |
| `perf-engineer` | Benchmarking, profiling, optimization | sonnet | Performance bottlenecks, load testing |

### Usage
```
# Audit latest changes
@security-auditor Review the changes in qbit_network/core/blockchain.py

# Implement a feature
@blockchain-dev Add WebSocket subscription for new blocks

# Run tests
@test-runner Run full test suite

# Update docs
@docs-writer Update CHANGELOG for v0.2.0

# Start a feature (tech-lead orchestrates everything)
@tech-lead Implement LevelDB backend for chain storage

# Plan next release
@product-owner Write requirements for v0.2.0 CLI wallet tool

# Design discussion
@protocol-designer Propose fork resolution mechanism for multi-validator

# Research
@researcher Compare ML-DSA-65 vs SLH-DSA for our use case

# Infrastructure
@devops Set up GitHub Actions CI with pytest

# CLI tool
@frontend-dev Build CLI wallet management tool

# Optimization
@perf-engineer Profile block validation and optimize nonce check
```

## Key Rules
1. **Never skip consensus validation** — all blocks (self-produced or received) must pass `validate_block()`
2. **Keep indices in sync** — `_pool_ids`, `_chain_tx_ids`, `_tx_by_id` must always reflect reality
3. **Validate all inputs** — every RPC param, every P2P field, every `from_dict` field
4. **Atomic writes only** — tempfile + os.replace for chain.json and wallet files
5. **Lock before nonce** — use `_lock_for(address)` when computing nonce + signing + submitting

## Quick Commands
```bash
# Run node
python3 run_node.py

# Run with custom config
python3 run_node.py --rpc-port 8546 --p2p-port 9001 --data-dir ./testchain

# Allow LAN peers (for testing)
QBIT_ALLOW_PRIVATE_PEERS=1 python3 run_node.py --peers 192.168.1.2:9000
```

## File Layout
```
qbit_network/crypto/   → PQC primitives (zero business logic)
qbit_network/core/     → Blockchain state machine
qbit_network/network/  → P2P + RPC
qbit_network/node.py   → Orchestrator
docs/            → Architecture, protocol, security docs
tracker/         → Audit log, issues, features, changelog
```
