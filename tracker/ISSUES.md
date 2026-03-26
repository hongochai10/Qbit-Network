# Issue Tracker

## Open Issues

None. All tracked issues are closed as of v0.6.0 (2026-03-26).

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

See [AUDIT_LOG.md](AUDIT_LOG.md) for the full audit trail (197+ issues across 17 rounds).

## Final Project Summary (v0.6.0, 2026-03-26)

- All 17 tracked issues closed
- 0 open issues, 0 accepted risks with outstanding mitigations
- 17 audit rounds completed
- 1358 tests passing
