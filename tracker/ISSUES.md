# Issue Tracker

## Open Issues

### HIGH Priority

| ID | Category | Description | Status |
|----|----------|-------------|--------|
| ISS-001 | Security | Python `bytes` immutable — secret keys persist in heap after GC | Open — needs C extension |

### MEDIUM Priority

| ID | Category | Description | Status |
|----|----------|-------------|--------|
| ISS-007 | Scalability | No chain pruning — disk grows without limit | Open — needs pruning strategy |
| ISS-009 | Security | Sybil/Eclipse attacks possible with no peer reputation | Open — needs reputation system |
| ISS-010 | UX | No key revocation mechanism — compromised keys can't be invalidated | Open — needs governance |
| ISS-016 | Security | TLS implemented (--tls-cert/--tls-key) but self-signed cert UX needs improvement | Open — add ACME/Let's Encrypt support |

### LOW Priority

| ID | Category | Description | Status |
|----|----------|-------------|--------|
| ISS-012 | Code | `get_nonce` naming misleading — returns next expected, not current | Open — rename deferred to Sprint 2 (SQLite migration) |
| ISS-013 | Protocol | Reverse-order blocks in sync silently discarded — no reordering | Open |
| ISS-014 | Protocol | `getSharedWithMe` doesn't verify wallet ownership | Open — by design |
## Closed Issues

| ID | Category | Description | Resolution | Closed |
|----|----------|-------------|------------|--------|
| ISS-002 | Security | P2P not authenticated — server-side HELLO_AUTH handler not implemented | Full 3-step ML-DSA-65 challenge-response auth (server + client) implemented in v0.3.0-sprint1 | 2026-03-25 |
| ISS-003 | Protocol | No fork resolution — permanent divergence | Pure longest-chain rule in v0.2.0, simplified v0.2.1 | 2026-03-25 |
| ISS-004 | Security | Shared secrets over HTTP cleartext | TLS support added in v0.2.0 | 2026-03-25 |
| ISS-008 | Protocol | Block sig verification skipped for unknown validators on load | REGISTER_VALIDATOR tx type + on-chain validator registry implemented in v0.3.0-sprint1 | 2026-03-25 |
| ISS-015 | CI/CD | CI pipeline does not run adversarial or integration tests — only unit suite | 75 new tests (46 adversarial + 29 integration); CI split into 3 parallel jobs in v0.3.0-sprint1 | 2026-03-25 |
| ISS-017 | Client | CLI missing STORE/SHARE | CLI store/share commands added in v0.2.1 | 2026-03-25 |
| ISS-005 | Protocol | Unsolicited MSG_BLOCKS can cause chain-split between honest nodes | Request-ID correlation added in v0.2.0 — unsolicited blocks rejected | 2026-03-25 |
| ISS-006 | Scalability | In-memory chain — all blocks/indices in RAM | LevelDB/SQLite persistent backend added in v0.2.0 | 2026-03-25 |
| ISS-011 | Code | Consensus nonce validation O(n^2) — could precompute sender counts | Precomputed sender-count map; validation now O(n) in v0.2.0 | 2026-03-25 |

See [AUDIT_LOG.md](AUDIT_LOG.md) for the 137+ closed issues from 12 audit rounds, plus 14 from Round 13 (151+ total).
