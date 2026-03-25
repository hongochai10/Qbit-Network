# Issue Tracker

## Open Issues

### HIGH Priority

| ID | Category | Description | Status |
|----|----------|-------------|--------|
| ISS-001 | Security | Python `bytes` immutable — secret keys persist in heap after GC | Open — needs C extension |
| ISS-002 | Security | P2P messages not authenticated (no Noise/TLS) — MITM possible | Open — protocol redesign |

### MEDIUM Priority

| ID | Category | Description | Status |
|----|----------|-------------|--------|
| ISS-007 | Scalability | No chain pruning — disk grows without limit | Open — needs pruning strategy |
| ISS-008 | Protocol | Block sig verification skipped for unknown validators on load | Open — needs validator key distribution |
| ISS-009 | Security | Sybil/Eclipse attacks possible with no peer reputation | Open — needs reputation system |
| ISS-010 | UX | No key revocation mechanism — compromised keys can't be invalidated | Open — needs governance |
| ISS-015 | CI/CD | CI pipeline does not run adversarial or integration tests — only unit suite | Open — expand test matrix |
| ISS-016 | Security | TLS termination is external (reverse proxy) — RPC server itself still plaintext | Open — accepted for v0.2.0; full in-process TLS deferred to v0.3.0 |

### LOW Priority

| ID | Category | Description | Status |
|----|----------|-------------|--------|
| ISS-012 | Code | `get_nonce` naming misleading — returns next expected, not current | Open |
| ISS-013 | Protocol | Reverse-order blocks in sync silently discarded — no reordering | Open |
| ISS-014 | Protocol | `getSharedWithMe` doesn't verify wallet ownership | Open — by design |
| ISS-017 | Client | CLI tool does not expose STORE or SHARE workflows — wallet and notarize only | Open — planned v0.3.0 |

## Closed Issues

| ID | Category | Description | Resolution | Closed |
|----|----------|-------------|------------|--------|
| ISS-003 | Protocol | No fork resolution mechanism — conflicting blocks cause permanent divergence | Longest valid chain rule implemented in v0.2.0 | 2026-03-25 |
| ISS-004 | Security | Shared secrets returned over HTTP cleartext via RPC | TLS support added (reverse-proxy mode) in v0.2.0 | 2026-03-25 |
| ISS-005 | Protocol | Unsolicited MSG_BLOCKS can cause chain-split between honest nodes | Request-ID correlation added in v0.2.0 — unsolicited blocks rejected | 2026-03-25 |
| ISS-006 | Scalability | In-memory chain — all blocks/indices in RAM | LevelDB/SQLite persistent backend added in v0.2.0 | 2026-03-25 |
| ISS-011 | Code | Consensus nonce validation O(n^2) — could precompute sender counts | Precomputed sender-count map; validation now O(n) in v0.2.0 | 2026-03-25 |

See [AUDIT_LOG.md](AUDIT_LOG.md) for the 104 closed issues from 9 audit rounds.
