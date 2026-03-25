# Issue Tracker

## Open Issues

### HIGH Priority

| ID | Category | Description | Status |
|----|----------|-------------|--------|
| ISS-001 | Security | Python `bytes` immutable — secret keys persist in heap after GC | Open — needs C extension |
| ISS-002 | Security | P2P messages not authenticated (no Noise/TLS) — MITM possible | Open — protocol redesign |
| ISS-003 | Protocol | No fork resolution mechanism — conflicting blocks cause permanent divergence | Open — needs finality gadget |
| ISS-004 | Security | Shared secrets returned over HTTP cleartext via RPC | Open — deploy behind TLS |
| ISS-005 | Protocol | Unsolicited MSG_BLOCKS can cause chain-split between honest nodes | Open — needs request-ID correlation |

### MEDIUM Priority

| ID | Category | Description | Status |
|----|----------|-------------|--------|
| ISS-006 | Scalability | In-memory chain — all blocks/indices in RAM | Open — needs DB backend |
| ISS-007 | Scalability | No chain pruning — disk grows without limit | Open — needs pruning strategy |
| ISS-008 | Protocol | Block sig verification skipped for unknown validators on load | Open — needs validator key distribution |
| ISS-009 | Security | Sybil/Eclipse attacks possible with no peer reputation | Open — needs reputation system |
| ISS-010 | UX | No key revocation mechanism — compromised keys can't be invalidated | Open — needs governance |

### LOW Priority

| ID | Category | Description | Status |
|----|----------|-------------|--------|
| ISS-011 | Code | Consensus nonce validation O(n^2) — could precompute sender counts | Open |
| ISS-012 | Code | `get_nonce` naming misleading — returns next expected, not current | Open |
| ISS-013 | Protocol | Reverse-order blocks in sync silently discarded — no reordering | Open |
| ISS-014 | Protocol | `getSharedWithMe` doesn't verify wallet ownership | Open — by design |

## Closed Issues

See [AUDIT_LOG.md](AUDIT_LOG.md) for the 101 closed issues from 9 audit rounds.
