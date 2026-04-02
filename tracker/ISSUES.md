# Issue Tracker

## Round 36 — CEO Comprehensive Audit (2026-04-03)

**Audit type:** Full codebase + tracker + documentation audit
**Findings:** No new code vulnerabilities. Audit focused on project health, process improvements, and sprint planning.

### Security Audit New Findings (7 new)

| ID | Severity | Description | Owner | Priority | Status |
|----|----------|-------------|-------|----------|--------|
| R36-M01 | MEDIUM | `validate_block` does not verify `chain_id` on TXs — cross-chain replay | Security Engineer | P0 | todo |
| R36-M02 | MEDIUM | EVIDENCE TX mined as "success" receipt even when double-sign verification fails silently | Security Engineer | P2 | todo |
| R36-L01 | LOW | Block `from_dict` baseFee upper bound missing (confirms R31-003) | Senior Backend | P2 | todo |
| R36-L02 | LOW | Fee param accepts 2^63 exactly — state trie 8-byte overflow on fee multiplication | Senior Backend | P2 | todo |
| R36-L03 | LOW | Epoch rewards not restored from SQLite on reload — delegators lose partial-epoch rewards | Senior Backend | P2 | todo |
| R36-L04 | LOW | `_rebuild_state_trie` crashes on temporary negative balances during multi-block rollback | Senior Backend | P1 | todo |
| R36-I01 | INFO | `qv_nodeInfo` public RPC exposes local wallet count and validator address | — | P3 | todo |

### Confirmed Fixed (previously open)
- R33-H02: TRANSFER rollback — **fixed** with `_rollback_debit` in ledger.py
- R32-F04: Epoch reward front-running — **fixed** with balance_snapshot in staking.py
- R31-001: REST _submit_evidence injection — **fixed** with allowlist in rest_api.py
- R33-M05: _state_snapshots growth — **partially fixed** with pruning at MAX_REORG_DEPTH

### Actions Taken
| Action | Description | Paperclip Issue |
|--------|-------------|-----------------|
| Plan created | Comprehensive improvement & feature matrix with 4-sprint deployment plan | TEC-1248 (plan document) |
| Sprint 0 tasks | 5 IMMEDIATE tasks: R33-H01, R33-H02, R32-F02, R30-001, push commits | TEC-1250 to TEC-1254 |
| Sprint 1 tasks | 4 Security hardening tasks: R31-001, R32-F03, R33-M05/M03, R32-F04 | TEC-1255 to TEC-1258 |
| Sprint 2 tasks | 2 Performance + CI tasks: perf bundle, CI enhancement | TEC-1259 to TEC-1260 |
| Sprint 3 tasks | 1 Documentation sync task | TEC-1261 |

### Key Observations
- R33-M04 (double height decrement) and R34-H01 confirmed **done** in prior rounds
- R35-H01 (_pending_debits O(1) cache) confirmed **done** in commit e417eaa
- R35-H02 (Docker package pinning) confirmed **done** in commit 69b6c10
- _events_by_block, _events_by_type, _receipts, _last_epoch_distributions pruning confirmed **done** in commit d2896d7
- 62 commits ahead of origin/main — needs immediate push
- Test count: 3,350 (up from 3,325 in R33), 91% coverage maintained

### Updated Open Issue Count (post R36 audit)
- **1 HIGH** (R33-H01) — R33-H02 confirmed fixed
- **13 MEDIUM** (12 prior + 2 new R36-M01/M02 − 1 fixed R31-001)
- **19 LOW** (16 prior + 4 new R36-L01/L02/L03/L04 − 1 confirmed R31-003=R36-L01)
- **4 INFO** (3 accepted + 1 new R36-I01)
- **Total: 28 → 32 open** (7 new findings, 4 confirmed fixes from prior rounds)

---

## Round 35 Findings (2026-04-02) — Memory & Docker Hardening

| ID | Severity | Description | Status |
|----|----------|-------------|--------|
| R35-H01 | HIGH | _pending_debits O(1) cache eliminates quadratic pool scan | **done** (commit e417eaa) |
| R35-H02 | HIGH | Docker package version pinning via requirements.lock | **done** (commit 69b6c10) |
| R35-L01 | LOW | Prune _events_by_block, _events_by_type, _receipts, _last_epoch_distributions beyond MAX_REORG_DEPTH | **done** (commit d2896d7) |

---

## Round 34 Findings (2026-04-02) — Batch Security Fixes

| ID | Severity | Description | Status |
|----|----------|-------------|--------|
| R34-M01 | MEDIUM | SSRF resolver missing multicast/unspecified checks (webhooks.py) | **done** — also resolves R33-L06 |
| R34-M02 | MEDIUM | SQLiteStore.close() thread leak — only closes current thread connection | **done** — track all conns in `_all_conns` |
| R34-L01 | LOW | Epoch reward accumulator not reset for validators without delegators | **done** — reset all validators after distribution |
| R34-L02 | LOW | Pool sender count desync on key revocation purge | **done** — batch update replaces per-item decrement |

## Round 33 Findings (2026-04-02) — CEO Full Audit + Security Auditor Review

### HIGH — P0 Critical Security

| ID | Severity | Description | Owner | Priority | Status |
|----|----------|-------------|-------|----------|--------|
| R33-H01 | HIGH | SQLite reload skips EVIDENCE TX replay — slashed validators regain full stake | security-engineer | P0 | todo (TEC-1204) |
| R33-H02 | HIGH | TRANSFER rollback uses min(amount,bal) — QBIT created from nothing on reorg | security-engineer | P0 | todo (TEC-1205) |
| RS-1 | HIGH | liboqs version mismatch: Dockerfile 0.12.0 vs README 0.15.0 | devops-engineer | P0 | done (TEC-1206) — all pinned to 0.15.0 |
| RS-2 | HIGH | stateRoot warn-but-accept on mismatch — Byzantine validator undetected | senior-backend-engineer | P0 | **done** (TEC-1207) — hard-reject on empty/mismatched state_root, 3327 tests pass |

### MEDIUM — P1/P2 Stability + New Findings

| ID | Severity | Description | Owner | Priority | Status |
|----|----------|-------------|-------|----------|--------|
| R33-M01 | MEDIUM | _pending_debits double-count risk + O(n) pool scan | senior-backend-engineer | P1 | todo (extends R32-F02) |
| R33-M02 | MEDIUM | _find_validator_pk_in_chain O(n) scan during rollback | founding-engineer | P2 | todo |
| R33-M03 | MEDIUM | Reputation score decay toward 0 instead of DEFAULT_SCORE — idle peers unfairly penalized | senior-backend-engineer | P1 | **done** (TEC-1257) — decay formula already correct: `DEFAULT_SCORE + (score - DEFAULT_SCORE) * DECAY_RATE`, stress tests added |
| R33-M04 | MEDIUM | Double height decrement in _append_block_inner_safe failure path — chain height corruption | founding-engineer | P1 | **done** (TEC-1222) — removed duplicate `self._height -= 1`, regression test added |
| R33-M05 | MEDIUM | _state_snapshots memory growth unbounded — OOM risk on long-running chains | senior-backend-engineer | P1 | **done** (TEC-1257) — pruning active via `pop(idx - MAX_REORG_DEPTH - 1)`, 500-block stability test added |
| R33-M06 | MEDIUM | Runtime get_block() trusts SQLite data without block hash verification | senior-backend-engineer | P2 | todo |

### LOW — P2/P3

| ID | Severity | Description | Owner | Priority | Status |
|----|----------|-------------|-------|----------|--------|
| R33-L03 | LOW | _last_epoch_distributions grows unbounded | senior-backend-engineer | P3 | todo |
| R33-L04 | LOW | _events_by_type list grows unbounded | senior-backend-engineer | P3 | todo |
| R33-L05 | LOW | Receipt index rebuild O(blocks * receipts) on load | database-architect | P3 | todo |
| R33-L06 | LOW | Webhook SSRF resolver missing .is_multicast/.is_unspecified check | security-engineer | P2 | **done** (R34-M01) — multicast + unspecified added to both resolver and registration |
| R33-L07 | LOW | _drain_pool does not evict stale-nonce TXs after block mined | senior-backend-engineer | P2 | todo (extends R32-F07) |

## Open Issues (Round 26 — 2026-03-30)

| ID | Severity | Description | Owner | Priority | Status |
|----|----------|-------------|-------|----------|--------|
| R26-001 | HIGH | RPC webhook methods missing from PROTECTED_METHODS — unauthenticated webhook registration | security-auditor | P0 | done |
| R26-002 | MEDIUM | Dashboard static file scope too broad — serves entire dashboard/ dir | blockchain-dev | P1 | done |
| R26-003 | MEDIUM | RPC list params positional unpacking bypasses validation | blockchain-dev | P1 | done |
| R26-004 | MEDIUM | TLS key non-atomic write in rpc.py _generate_self_signed | blockchain-dev | P1 | done |
| R26-005 | LOW | _rpc_get_logs no event_type validation | blockchain-dev | P2 | done (node.py:1099-1104) |
| R26-006 | LOW | get_token_holders O(n) scan, no pagination | perf-engineer | P2 | done (R28-007) |
| R26-007 | LOW | get_address_tokens O(n) scan, no pagination | perf-engineer | P2 | done (R28-007) |
| R26-008 | INFO | Duplicate TLS generation code paths (rpc.py vs tls_manager.py) | blockchain-dev | P3 | todo |
| R26-009 | INFO | Info endpoint exposes webhook methods as public (depends R26-001) | — | P3 | done |

## New Findings (Round 27 — CEO Audit 2026-03-30)

| ID | Severity | Description | Owner | Priority | Status |
|----|----------|-------------|-------|----------|--------|
| R27-001 | MEDIUM | Webhook SSRF DNS fallthrough — gaierror falls through to aiohttp resolve (DNS rebinding) | security-engineer | P1 | done (TEC-926) |
| R27-002 | LOW | WebSocket heartbeat constants not passed to WebSocketResponse — zombie connections | senior-backend-engineer | P1 | done (R29 verified) |
| R27-003 | MEDIUM | _block_level_events not initialized in __init__(), lost on restart | senior-backend-engineer | P2 | done (TEC-926) |
| R27-004 | LOW | Webhook creates new aiohttp.ClientSession per event delivery — resource waste | senior-backend-engineer | P2 | done (R29 verified) |

## New Findings (Round 28 — CEO Audit 2026-03-31)

| ID | Severity | Description | Owner | Priority | Status |
|----|----------|-------------|-------|----------|--------|
| R28-001 | MEDIUM | _compute_fee_defaults silently casts string/float fee params via int() — type safety bypass | security-engineer | P1 | done (TEC-926) |
| R28-002 | MEDIUM | State trie key injection via token_id colon — defense-in-depth gap | security-engineer | P2 | done (TEC-895) |
| R28-003 | MEDIUM | _wallet_locks unbounded growth under concurrent raw TX — DoS memory exhaustion | senior-backend-engineer | P1 | done (R29 verified) |
| R28-004 | LOW | ISSUE_TOKEN token_id not in receipt/Merkle — silent fork risk in legacy mode | senior-backend-engineer | P2 | done (TEC-897) |
| R28-005 | LOW | REST _txs_by_sender materializes full list for pagination total — memory spike | founding-engineer | P2 | todo (TEC-896) |
| R28-006 | LOW | P2P _on_connect dispatches HELLO_AUTH before auth completes | senior-backend-engineer | P3 | todo |
| R28-007 | LOW | get_token_holders/get_address_tokens block event loop on large datasets (extends R26-006/007) | senior-backend-engineer | P1 | done (TEC-892) |
| R28-008 | INFO | SecureBytes cannot zero source bytes from keygen — CPython limitation | — | — | accepted |

## New Findings (Round 29 — CEO Comprehensive Audit 2026-03-31)

| ID | Severity | Description | Owner | Priority | Status |
|----|----------|-------------|-------|----------|--------|
| R29-001 | MEDIUM | get_token_holders() materializes full holder list before pagination — DoS via public endpoint | security-engineer | P1 | done (TEC-1155) |
| R29-002 | MEDIUM | Inbound P2P connections bypass _is_safe_peer() — private IPs accepted | security-engineer | P1 | done (p2p.py:951 _is_safe_inbound_ip) |
| R29-003 | LOW | qv_getStateProofAt accepts arbitrary trie key — unauthenticated token balance probing | security-engineer | P2 | todo (TEC-925) |
| R29-004 | LOW | Inbound P2P first message uses readline() — incompatible with binary wire format | senior-backend-engineer | P3 | todo |
| R29-005 | INFO | Version string mismatch __init__.py (0.2.0) vs config.py (0.8.0) | qa-engineer | P2 | todo (TEC-928) |

## New Findings (Round 30 — CEO Audit 2026-04-02)

| ID | Severity | Description | Owner | Priority | Status |
|----|----------|-------------|-------|----------|--------|
| R30-001 | MEDIUM | Unbounded TRANSFER/STAKE/MINT amount — pool DoS via big integers in _pending_debits | security-engineer | P1 | todo (TEC-1110) |
| R30-002 | MEDIUM | P2P-received transactions bypass fee param upper bound (2^63 cap in node.py only) | security-engineer | P1 | done (Transaction.from_dict:397-402) |
| R30-003 | MEDIUM | State proof endpoint arbitrary trie key enumeration — token balance probing (extends R29-003) | security-engineer | P1 | todo (TEC-1111) |
| R30-004 | LOW | TRANSFER amount validation inconsistency between validate_payload and from_dict | security-engineer | P2 | todo (TEC-1110) |
| R30-005 | LOW | `_auth_attempts` dict unbounded growth under IP rotation attack | founding-engineer | P2 | done (p2p.py:870-871 LRU cap) |
| R30-006 | LOW | REST `/pool` exposes tx type distribution without auth | founding-engineer | P3 | todo |
| R30-007 | LOW | `state_tree.get_proof()` uses O(n) list.index instead of bisect | founding-engineer | P2 | done (state_tree.py:93 bisect) |
| R30-008 | INFO | Receipt events contain unsanitized user data — webhook XSS risk for consumers | — | — | accepted |
| R30-009 | INFO | WebSocket auth bypass path when no token configured — not exploitable | — | — | accepted |

## New Findings (Round 31 — CEO Comprehensive Audit 2026-04-02)

| ID | Severity | Description | Owner | Priority | Status |
|----|----------|-------------|-------|----------|--------|
| R31-001 | MEDIUM | REST `_submit_evidence` passes raw body as **kwargs — param injection | security-engineer | P1 | todo (TEC-1129) |
| R31-002 | MEDIUM | REST `_get_token_holders`/`_get_address_tokens` missing pagination validation | senior-backend-engineer | P1 | todo (TEC-1130) |
| R31-003 | LOW | Block `from_dict` does not validate `baseFee` type or range | senior-backend-engineer | P2 | todo (TEC-1135) |
| R31-004 | LOW | `list_tokens` returns unstable ordering across nodes | senior-backend-engineer | P2 | **done** (TEC-1136) |
| R31-005 | LOW | `_deliver_block_webhooks` materializes all events before filtering | — | P3 | todo |
| R31-006 | LOW | WebSocket auth bypass when no token configured | — | — | accepted |
| R31-007 | INFO | `DYNAMIC_FEE_ACTIVATION_HEIGHT` default 2^63 effectively disables dynamic fees | senior-backend-engineer | P2 | todo (TEC-1137) |

Accepted risks (no action required):
- R25-004 (LOW): SQLite synchronous=NORMAL — self-corrects via peer re-sync
- R25-006 (LOW): _pending_debits O(n) scan — known since R16-002
- R25-008 (INFO): TLS uses classical SECP256R1 — transport-only
- R25-009 (INFO): No TX timestamp age check at block validation — not exploitable
- R28-008 (INFO): SecureBytes cannot zero immutable bytes from keygen — CPython limitation

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

## New Findings (Round 32 — CEO Full Audit 2026-04-02)

| ID | Severity | Description | Owner | Priority | Status |
|----|----------|-------------|-------|----------|--------|
| R32-F01 | MEDIUM | SQLite connection not thread-safe across async context — reload races with concurrent reads | database-architect | P2 | todo (TEC-1188) |
| R32-F02 | MEDIUM | Token operation QBIT fees missing from _pending_debits — pool admits unfunded TXs | senior-backend-engineer | P0 | todo (TEC-1183) |
| R32-F03 | MEDIUM | No post-load integrity verification — state root not compared after SQLite reload | database-architect | P1 | todo (TEC-1188) |
| R32-F04 | MEDIUM | Epoch reward front-running — validator self-transfer reduces delegator rewards | security-engineer | P1 | todo (TEC-1189) |
| R32-F05 | LOW | Evidence rollback does not restore slashed stake — permanent loss after reorg | senior-backend-engineer | P2 | todo |
| R32-F06 | LOW | _ChainProxy.__iter__ O(n) SQLite queries — performance bottleneck | founding-engineer | P2 | todo |
| R32-F07 | LOW | _drain_pool does not clean stale nonce entries after mined TXs | senior-backend-engineer | P2 | todo |
| R32-F08 | LOW | RPC batch processing sequential — DoS via slow batch queries | senior-backend-engineer | P2 | todo |

See [AUDIT_LOG.md](AUDIT_LOG.md) for the full audit trail (340+ issues across 36 rounds).

## Project Summary (v0.8.0, 2026-04-03)

- 22 closed issues (17 original + 5 R25 resolved)
- R26: 5 done, 1 open (R26-008), 1 INFO done
- R27: 4 fixed, 0 open
- R28: 5 fixed, 2 open (R28-005/006), 1 accepted (R28-008)
- R29: 2 done, 2 open (R29-003/004), 1 open INFO (R29-005)
- R30: 3 done, 4 open (R30-001/003/004/006), 2 accepted (R30-008/009)
- R31: 7 findings, 6 open, 1 accepted (R31-006)
- R32: 8 findings (4 MED, 4 LOW), 8 open
- R33: 15 findings (4 HIGH, 6 MED, 5 LOW), 4 done (RS-1, RS-2, R33-M04, R33-L06), 11 open
- R34: 4 findings, 4 done
- R35: 3 findings, 3 done (e417eaa, 69b6c10, d2896d7)
- **R36: Process audit — 12 subtasks created on Paperclip (TEC-1250 to TEC-1261)**
- 8 accepted risks (no action required)
- **36 audit rounds completed**
- 3,350 tests collected, 91% coverage
- **28 open issues total (2 HIGH, 12 MED, 16 LOW, 3 INFO)**
- 12 Paperclip subtasks created for sprint execution

## Sprint 1 Subtasks (TEC-1180)

| Task | Title | Assignee | Priority |
|------|-------|----------|----------|
| TEC-1181 | Fix REST param injection R31-001 + state proof R30-003 | security-engineer | CRITICAL |
| TEC-1183 | Fix token fees _pending_debits R32-F02 + pagination R31-002 | senior-backend-engineer | HIGH |
| TEC-1184 | Fix unbounded amount R30-001 + validation R30-004 | security-engineer | HIGH |
| TEC-1185 | CI/CD pipeline + Docker | devops-engineer | HIGH |
| TEC-1186 | REST API test coverage 51% → 80%+ | qa-engineer | HIGH |
| TEC-1187 | Documentation sync for v0.8.0 R32 | technical-writer | HIGH |
| TEC-1188 | Post-load state root R32-F03 + SQLite safety R32-F01 | database-architect | HIGH |
| TEC-1189 | Epoch reward front-running R32-F04 | security-engineer | HIGH |
| TEC-1190 | Prometheus metrics + health check + logging | infrastructure-engineer | MEDIUM |
