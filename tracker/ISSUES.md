# Issue Tracker

## Open Issues (Round 26 — 2026-03-30)

| ID | Severity | Description | Owner | Priority | Status |
|----|----------|-------------|-------|----------|--------|
| R26-001 | HIGH | RPC webhook methods missing from PROTECTED_METHODS — unauthenticated webhook registration | security-auditor | P0 | done |
| R26-002 | MEDIUM | Dashboard static file scope too broad — serves entire dashboard/ dir | blockchain-dev | P1 | done |
| R26-003 | MEDIUM | RPC list params positional unpacking bypasses validation | blockchain-dev | P1 | todo |
| R26-004 | MEDIUM | TLS key non-atomic write in rpc.py _generate_self_signed | blockchain-dev | P1 | todo |
| R26-005 | LOW | _rpc_get_logs no event_type validation | blockchain-dev | P2 | todo |
| R26-006 | LOW | get_token_holders O(n) scan, no pagination | perf-engineer | P2 | todo |
| R26-007 | LOW | get_address_tokens O(n) scan, no pagination | perf-engineer | P2 | todo |
| R26-008 | INFO | Duplicate TLS generation code paths (rpc.py vs tls_manager.py) | blockchain-dev | P3 | todo |
| R26-009 | INFO | Info endpoint exposes webhook methods as public (depends R26-001) | — | P3 | done |

Accepted risks (no action required):
- R25-004 (LOW): SQLite synchronous=NORMAL — self-corrects via peer re-sync
- R25-006 (LOW): _pending_debits O(n) scan — known since R16-002
- R25-008 (INFO): TLS uses classical SECP256R1 — transport-only
- R25-009 (INFO): No TX timestamp age check at block validation — not exploitable

## Closed Issues

| ID | Category | Description | Resolution | Closed |
|----|----------|-------------|------------|--------|
| ISS-001 | Security | Python `bytes` immutable — secret keys persist in heap after GC | `SecureBytes` ctypes buffer with explicit `zero()`; `Wallet.close()` zeros all secret key material on shutdown (v0.4.0-sprint3) | 2026-03-25 |
| ISS-002 | Security | P2P not authenticated — server-side HELLO_AUTH handler not implemented | Full 3-step ML-DSA-65 challenge-response auth (server + client) implemented in v0.3.0-sprint1 | 2026-03-25 |
| ISS-003 | Protocol | No fork resolution — permanent divergence | Pure longest-chain rule in v0.2.0, simplified v0.2.1 | 2026-03-25 |
| ISS-004 | Security | Shared secrets over HTTP cleartext | TLS support added in v0.2.0 | 2026-03-25 |
| ISS-005 | Protocol | Unsolicited MSG_BLOCKS can cause chain-split between honest nodes | Request-ID correlation added in v0.2.0 — unsolicited blocks rejected | 2026-03-25 |
| ISS-006 | Scalability | In-memory chain — all blocks/indices in RAM | LevelDB/SQLite persistent backend added in v0.2.0 | 2026-03-25 |
| ISS-007 | Scalability | No chain pruning — disk grows without limit | `Blockchain.prune()` removes block+tx SQLite rows for blocks older than `height - PRUNING_RETENTION`; all indices preserved (v0.4.0-sprint3) | 2026-03-25 |
| ISS-008 | Protocol | Block sig verification skipped for unknown validators on load | REGISTER_VALIDATOR tx type + on-chain validator registry implemented in v0.3.0-sprint1 | 2026-03-25 |
| ISS-009 | Security | Sybil/Eclipse attacks possible with no peer reputation | dPoS slashing for misbehavior, `PeerReputation` scoring with 8 event types, P2P encrypted channel, connection deduplication, HELLO_AUTH mutual authentication (v0.4.0-sprint3) | 2026-03-25 |
| ISS-010 | UX | No key revocation mechanism — compromised keys can't be invalidated | `REVOKE_KEY` tx type implemented in v0.3.0-sprint2 | 2026-03-25 |
| ISS-011 | Code | Consensus nonce validation O(n^2) — could precompute sender counts | Precomputed sender-count map; validation now O(n) in v0.2.0 | 2026-03-25 |
| ISS-012 | Code | `get_nonce` naming misleading — returns next expected, not current | `get_next_nonce()` alias added in v0.3.0-sprint2 | 2026-03-25 |
| ISS-013 | Protocol | Reverse-order blocks in sync silently discarded — no reordering | Accepted by design; sync retry on next cycle handles reordering naturally | 2026-03-25 |
| ISS-014 | Protocol | `getSharedWithMe` doesn't verify wallet ownership | Accepted by design; auth token provides sufficient access control | 2026-03-25 |
| ISS-015 | CI/CD | CI pipeline does not run adversarial or integration tests — only unit suite | 75 new tests (46 adversarial + 29 integration); CI split into 3 parallel jobs in v0.3.0-sprint1 | 2026-03-25 |
| ISS-016 | Security | TLS implemented but self-signed cert UX needs improvement | `TLSManager` auto-generates, renews, and hot-reloads self-signed certificates; `--tls-auto` flag (v0.4.0-sprint3) | 2026-03-25 |
| ISS-017 | Client | CLI missing STORE/SHARE | CLI store/share commands added in v0.2.1 | 2026-03-25 |
| R25-001 | Security | WebSocket subscriptions unauthenticated | Bearer token auth added to /ws upgrade endpoint (`247bdb0`) | 2026-03-29 |
| R25-002 | Security | REST auth per-handler pattern fragile | Refactored to route-group auth middleware (`703ce72`) | 2026-03-29 |
| R25-003 | Security | PoA skip-slot uses wall clock | Fixed to use block-internal timestamps (`412e838`) | 2026-03-29 |
| R25-005 | Security | Wallet files saved without encryption | Added directory permissions and plaintext warning (`bb21a88`) | 2026-03-29 |
| R25-007 | Security | CORS wildcard allows cross-origin probing | Default restrictive CORS, added --cors-origin flag (`3a64749`) | 2026-03-29 |

See [AUDIT_LOG.md](AUDIT_LOG.md) for the full audit trail (258+ issues across 26 rounds).

## Project Summary (v0.8.0, 2026-03-30)

- 22 closed issues (17 original + 5 R25 resolved)
- 9 new R26 issues open (1 HIGH, 3 MEDIUM, 3 LOW, 2 INFO)
- 4 accepted risks (no action required)
- 26 audit rounds completed
- 2,179 tests passing, 78% code coverage
