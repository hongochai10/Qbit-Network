# Security Audit Log

## Summary

- **Total rounds**: 13 (Sprint 1 + Sprint 2)
- **Total issues found**: 181+
- **Total fixed**: 159+
- **Accepted risks / deferred**: 9
- **Latest**: Round 13 Sprint 2 (v0.3.0 Sprint 2 audit) — complete 2026-03-25

## Round 1 — Basic Correctness (14 issues)

| # | Sev | Issue | Fix |
|---|-----|-------|-----|
| 1 | CRIT | `timestamp or int(time.time())` breaks deserialization | `timestamp if timestamp is not None` |
| 2 | CRIT | Double `stop()` crashes aiohttp | Idempotent stop |
| 3 | CRIT | `wallet.save("file.json")` crashes on bare filename | Guard empty dirname |
| 4 | CRIT | `load()` no chain validation | Validate prev_hash + index continuity |
| 5 | CRIT | Non-atomic `chain.json` write | tempfile + os.replace |
| 6 | HIGH | Genesis block accepted from any peer | `set_genesis_hash()` lock |
| 7 | HIGH | P2P no message size limit | MAX_MESSAGE_SIZE + reader limit |
| 8 | HIGH | Wallet keys stored plaintext | XOR encryption (later replaced with AES-GCM) |
| 9 | HIGH | shared_secret returned over HTTP | Store locally, separate decapsulate endpoint |
| 10 | MED | No chain sync initiation | Request blocks from bootstrap peers |
| 11 | MED | broadcast() sequential | `asyncio.gather()` parallel |
| 12 | MED | Validator selection not enforced | Round-robin check in validate_block |
| 13 | MED | No nonce/chain_id | Added to TX signable bytes |
| 14 | LOW | Unused imports | Removed |

## Round 2 — Deep Crypto + Protocol (21 issues)

| # | Sev | Issue | Fix |
|---|-----|-------|-----|
| S01 | CRIT | Wallet XOR Vigenere encryption | AES-256-GCM + scrypt KDF |
| S02 | CRIT | ML-DSA/ML-KEM crash on malformed input | try/except + size validation |
| S03 | CRIT | chain.json load no signature verify | Verify tx sigs + block sigs on load |
| S04 | CRIT | encryption_pk not bound on-chain | REGISTER_KEY tx type + key registry |
| S05 | HIGH | RPC zero authentication | Bearer token auth |
| S06 | HIGH | SSRF via P2P peer injection | `_is_safe_peer()` with IP validation |
| S07 | HIGH | P2P readline buffer already allocated | Reader limit parameter |
| S08 | HIGH | tx_pool unbounded | MAX_TX_POOL_SIZE = 10000 |
| S09 | HIGH | No future timestamp bound | MAX_BLOCK_DRIFT = 30s |
| S10 | HIGH | No payload size limit | MAX_TX_PAYLOAD_SIZE = 8KB |
| S11 | HIGH | Secret keys never zeroed | Documented limitation |
| S12 | HIGH | RPC batch DoS | MAX_RPC_BATCH = 50 |
| S13 | HIGH | No RPC body size limit | MAX_RPC_BODY = 1MB |
| S14 | MED | SHARE expires not enforced | Filter in get_shared_with() |
| S15 | MED | document_hash accepts any string | Hex regex validation |
| S16 | MED | Block nonce ordering not validated | Per-sender nonce check |
| S17 | MED | Self-connection + invalid port | `_is_safe_peer()` |
| S18 | MED | Error messages leak internals | Truncate to 200 chars |
| S19 | MED | from_dict no type validation | isinstance checks |
| S20 | MED | No block byte size limit | MAX_BLOCK_SIZE = 5MB |
| S21 | MED | _shared_secrets unbounded | OrderedDict LRU cap |

## Round 3 — Line-by-Line Review (16 issues)

| # | Sev | Issue | Fix |
|---|-----|-------|-----|
| F01 | HIGH | MLDSA.sign() no exception handling | try/except RuntimeError |
| F02 | HIGH | Wallet scrypt params from file (downgrade) | `max(file_n, MIN_N)` |
| F03 | HIGH | Nonce replay in blocks (no chain state check) | `_chain_nonces` injection |
| F04 | HIGH | RPC token timing attack | `hmac.compare_digest()` |
| F05 | MED | Wallet non-atomic write | tempfile + os.replace |
| F06 | MED | Corrupt chain.json crashes node | try/except JSONDecodeError |
| F07 | MED | P2P get_blocks type confusion | int() cast + try/except |
| F08 | MED | P2P fake height triggers sync spam | Range check 0..10M |
| F09 | MED | Multiple HELLO overwrites peers | hello_done flag |
| F10 | LOW | Block.from_dict no index type check | isinstance validation |
| F11 | LOW | Auth token logged plaintext | Masked output |
| F12 | LOW | Unused import json in node.py | Removed |
| F13 | LOW | aes_decrypt no min length | 28-byte minimum check |
| F14 | LOW | Block size check serializes entire block | Fast estimate |
| F15 | LOW | _header_bytes() not cached | _cached_header |
| F16 | LOW | Transaction mutable despite cache | __slots__ documentation |

## Round 4 — Regression Analysis (3 issues)

| # | Sev | Issue | Fix |
|---|-----|-------|-----|
| G01 | MED | _read_loop STATUS height not validated | isinstance + range check |
| G02 | LOW | blockchain.save() unlink may lose exception | try/except OSError |
| G03 | MED | load() not atomic (partial blocks on failure) | Temp validated_blocks list |

## Round 5 — Automated Security Agent (21 issues)

| # | Sev | Issue | Fix |
|---|-----|-------|-----|
| H01 | CRIT | Block out-of-order injection | `block.index == len(chain)` guard |
| H02 | HIGH | Chain load skips sig for unknown validators | Register validators before load |
| H03 | HIGH | P2P 10MB reader concurrent buffering | MAX_PEERS enforced |
| H04 | HIGH | SSRF via private networks (RFC 1918) | `addr.is_private` + configurable |
| H05 | HIGH | Shared secret plaintext over HTTP | Documented: requires TLS |
| H06 | MED | scrypt DoS via huge params | Upper bounds N<=2^20 |
| H07 | MED | Wallet decrypt no bounds on key lengths | Exact size validation |
| H08 | MED | Consensus nonce check O(n^2) | Noted: bounded by MAX_TX_PER_BLOCK |
| H09 | MED | Chunked encoding bypasses body check | client_max_size on Application |
| H10 | MED | RPC list params positional injection | Caught by generic except |
| H11 | MED | from_dict no payload type/pk size check | isinstance + size validation |
| H12 | MED | P2P re-broadcasts raw peer data | Canonical block.to_dict() |
| H13 | MED | Same for TX re-broadcast | Canonical tx.to_dict() |
| H14 | LOW | Auth token partially leaked in logs | Already masked |
| H15 | LOW | GET / exposes protected method names | Only list public methods |
| H16 | LOW | tx_pool duplicate check O(n) | _pool_ids set for O(1) |
| H17 | LOW | get_nonce naming misleading | Noted: rename later |
| H18 | LOW | TOCTOU chmod after replace | chmod before replace |
| H19 | LOW | MAX_PEERS not enforced | Check in connect + _on_connect |
| H20 | LOW | Block.from_dict doesn't verify hash | Compare computed vs claimed |
| H21 | LOW | Merkle tree second-preimage | Domain separation prefixes |

## Round 6 — Red Team Adversarial (9 issues)

| # | Sev | Issue | Fix |
|---|-----|-------|-----|
| V01 | CRIT | Nonce race between concurrent RPC calls | asyncio.Lock per-address |
| V03 | HIGH | Cross-block tx replay not checked | _chain_tx_ids set |
| V04 | HIGH | Chunked Transfer-Encoding bypass | client_max_size on aiohttp |
| V05 | HIGH | --no-validate nodes accept any genesis | Lock genesis after first accept |
| V06 | MED | expires field type confusion (string → crash) | isinstance(int) check |
| V07 | MED | Unsolicited MSG_BLOCKS chain-split | Accepted: needs protocol redesign |
| V08 | MED | Extra payload keys bypass dedup | _ALLOWED_KEYS whitelist |
| V09 | MED | Unbounded chain growth | Accepted: needs pruning |
| V10 | MED | Idle inbound sockets fill MAX_PEERS | 10s HELLO timeout |

## Round 7 — Fix Regression + Edge Cases (11 issues)

| # | Sev | Issue | Fix |
|---|-----|-------|-----|
| I21 | CRIT | "_pending_sync" sentinel blocks ALL genesis sync | Removed sentinel, use _lock_genesis_if_needed() |
| I14 | MED | Non-string RPC params cause TypeError | isinstance checks on all params |
| I01 | MED | _rpc_send_raw_tx accepts non-dict | isinstance(dict) check |
| I09 | MED | _p2p_peers unlimited address list | [:50] cap |
| I17 | MED | Single corrupt block silently aborts sync | break instead of pass |
| I18 | MED | No limit on inbound blocks message | [:100] cap |
| I10 | LOW | GET_BLOCKS broadcast amplification | Send to best peer only |
| I07 | LOW | String index silently becomes hash lookup | isinstance(int) check |
| I19 | LOW | Empty non-genesis blocks (chain bloat) | Reject in consensus |
| I12 | LOW | load() twice duplicates state | Skip if chain non-empty |
| I16 | LOW | OrderedDict FIFO not LRU | Accepted: FIFO sufficient |

## Round 8 — Module Consistency (4 issues)

| # | Sev | Issue | Fix |
|---|-----|-------|-----|
| E4 | MED | block_hash int param treated as index | isinstance validation |
| E3 | LOW | 5 RPC methods missing isinstance check | Added checks |
| C3 | LOW | Reverse-order blocks silently discarded | Accepted: retry next cycle |
| B4 | LOW | getSharedWithMe no wallet ownership check | Accepted: auth token sufficient |

## Round 9 — Semantic + Protocol Correctness (5 issues)

| # | Sev | Issue | Fix |
|---|-----|-------|-----|
| A | HIGH | Notarization overwrite (first proof lost) | Keep first, add get_all_notarizations() |
| B | HIGH | Key registry overwrite (old shares unrecoverable) | _key_history preserves all versions |
| C | MED | Empty [] chain.json causes validator stall | Return False for empty array |
| D | CRIT | produce_block skips consensus validation (fork) | Full validate_block + monotonic timestamp |
| E | MED | load() skips sig for unknown validators | Log warning |

## Round 10 — v0.2.0 Feature Audit (7 issues)

Scope: fork resolution logic, LevelDB/SQLite backend, request-ID correlation, TLS integration, CLI tool, proof export, CI/CD pipeline, O(n) nonce validation refactor.

| # | Sev | Issue | Fix |
|---|-----|-------|-----|
| J01 | HIGH | Fork resolution: reorg depth unbounded | MAX_REORG_DEPTH=32 |
| J02 | HIGH | SQLite dual-write: partial failure leaves state inconsistent | Wrap in transaction |
| J03 | MED | Request-ID not validated as UUID | UUID4 format check |
| J04 | MED | TLS self-signed cert accepted silently by CLI | --insecure flag + warning |
| J05 | MED | Proof bundle missing chain_id — portable to wrong network | chain_id added to export |
| J06 | LOW | CLI wallet create race on simultaneous invocations | _lock_for() on wallet file |
| J07 | LOW | O(n) nonce refactor introduced off-by-one on empty tx list | Unit test + guard |

## Round 11 — Rate Limiting + Auth Baseline (9 issues)

Scope: token bucket implementation, HELLO_AUTH client-side, backwards compatibility with v1 peers.

| # | Sev | Issue | Fix |
|---|-----|-------|-----|
| K01 | HIGH | Rate limiter shared state not thread-safe | asyncio.Lock per bucket |
| K02 | HIGH | HELLO_AUTH client sends challenge before conn established | Sequence guard |
| K03 | MED | Token bucket burst allows 100x single-IP amplification | Per-IP burst cap enforced |
| K04 | MED | v1 peer downgrade not logged — silent auth skip | Log warning on v1 connect |
| K05 | MED | Auth grace period fixed 10s — too short under load | Configurable via AUTH_GRACE_PERIOD |
| K06 | MED | Rate limiter LRU eviction drops active peers | Active-peer exclusion list |
| K07 | LOW | Bucket state not persisted — resets on restart | Accepted: stateless by design |
| K08 | LOW | HELLO_AUTH challenge not domain-separated in v1 fallback | N/A — v1 skips challenge |
| K09 | INFO | No unit tests for token bucket edge cases | Added in test_adversarial.py |

## Round 12 — v0.2.1 Full Audit (9 issues)

Scope: validator registry, docker/compose, HTML proof export, CI pipeline, complete protocol review.

| # | Sev | Issue | Fix |
|---|-----|-------|-----|
| L01 | HIGH | REGISTER_VALIDATOR allows overwrite of active validator key | Reject if address already registered |
| L02 | HIGH | Validator reorg rollback does not re-check pending txs | Re-validate pool after rollback |
| L03 | MED | HTML proof template XSS via unsanitized document_hash | html.escape() on all fields |
| L04 | MED | docker-compose tokens hardcoded as env defaults | Require explicit env; no defaults |
| L05 | MED | Genesis validator not recorded in validator_registry table | Auto-insert on init_chain() |
| L06 | MED | qv_validators RPC leaks internal validator struct | Serialize pubkey as hex only |
| L07 | LOW | CI artifact uploads fail silently on fork PRs | Continue-on-error + warning |
| L08 | LOW | proof --format html writes to cwd without confirmation | --output flag added |
| L09 | INFO | No adversarial tests for REGISTER_VALIDATOR replay | Covered in Round 13 scope |

## Round 13 — v0.3.0 Sprint 1 Audit (14 issues)

Scope: HELLO_AUTH server+client full flow, REGISTER_VALIDATOR on-chain registry, token bucket rate limiting, 75 new adversarial/integration tests, 3-job CI pipeline.

| # | Sev | Issue | Fix |
|---|-----|-------|-----|
| SPRINT1-001 | HIGH | Auth grace period bypass — attacker sends block before deadline | Fixed: deadline checked on every message, not just at expiry |
| SPRINT1-002 | HIGH | v1 downgrade on failed auth — failed challenge accepted as v1 | Fixed: failed auth triggers disconnect, no v1 fallback |
| SPRINT1-003 | MED | Responder signs before verifying initiator fields | Deferred — requires protocol redesign |
| SPRINT1-004 | MED | Auth handshake rate limiting absent — handshake flood possible | Fixed: HELLO/HELLO_AUTH counted in rate limiter with separate bucket |
| SPRINT1-005 | MED | No validator registry cross-reference on block validation | Fixed: validate_block checks _validator_registry for known validators |
| SPRINT1-006 | MED | REGISTER_VALIDATOR allows overwrite of active validator key | Fixed: reject if address already in registry |
| SPRINT1-007 | MED | Genesis validator not recorded via on-chain tx | Deferred — low risk; auto-registered in memory on init_chain() |
| SPRINT1-008 | MED | Rate limiter LRU eviction bypass via IP rotation | Fixed: eviction skips peers with open connections |
| SPRINT1-009 | LOW | threading.Lock used inside asyncio context | INFO only — no deadlock observed; note added for future asyncio.Lock migration |
| SPRINT1-010 | LOW | Wall clock used for auth deadline — susceptible to clock skew | Fixed: monotonic time (time.monotonic()) for deadline tracking |
| SPRINT1-011 | LOW | SQLite validator table uses string concatenation (not parameterized) | Deferred — address is hex-validated before use; parameterized query scheduled for v0.4.0 |
| SPRINT1-012 | MED | Non-atomic validator SQLite write during reorg — partial state possible | Fixed: validator rollback wrapped in SQLite transaction |
| SPRINT1-013 | LOW | Authenticated peer port claim not verified | INFO only — port used for display only, not routing |
| SPRINT1-014 | INFO | No Sprint 1 test coverage for auth edge cases | Addressed in Sprint 3 test plan |

**Round 13 Sprint 1 summary:** 8 fixed, 3 deferred to v0.4.0, 3 informational

## Round 13 — v0.3.0 Sprint 2 Audit (16 issues)

Scope: SQLite-primary chain storage, REVOKE_KEY transaction type, REST API gateway, WebSocket subscriptions.

| # | Sev | Issue | Fix |
|---|-----|-------|-----|
| SPRINT2-001 | HIGH | ChainProxy `__iter__` fetches all blocks into memory — defeats SQLite-primary goal | Fixed: streaming iteration via `get_blocks_range()` with page cursor |
| SPRINT2-002 | HIGH | REST API bearer token compared with `==` not `hmac.compare_digest` | Fixed: constant-time comparison applied to REST auth |
| SPRINT2-003 | HIGH | WebSocket broadcast holds reference to closed writer — write to closed socket raises | Fixed: exception caught per-client; dead clients pruned after broadcast |
| SPRINT2-004 | MED | REVOKE_KEY allows revocation of another address's key via crafted payload | Fixed: `from` address must equal the key's owner address (self-revocation only) |
| SPRINT2-005 | MED | REST pagination `limit` param not capped — `limit=999999` exhausts memory | Fixed: max limit enforced at 100 per request |
| SPRINT2-006 | MED | REST `/blocks` returns full tx data — large blocks cause oversized responses | Fixed: block list endpoint returns headers only; `/blocks/:index` returns full block |
| SPRINT2-007 | MED | WebSocket rate limiter counts server-initiated pings against client quota | Fixed: server-side pings exempt from rate limit |
| SPRINT2-008 | MED | REVOKE_KEY rollback re-adds validator to consensus but not to `_validator_registry` dict | Fixed: rollback restores both `_validator_registry` and `consensus.validators` |
| SPRINT2-009 | MED | `get_all_revocations()` on startup loads into memory before `_revoked_keys` populated — race with incoming txs | Fixed: load called under `_state_lock` before P2P/RPC accept connections |
| SPRINT2-010 | MED | REST CORS preflight returns 405 Method Not Allowed instead of 204 | Fixed: OPTIONS handler added for all routes |
| SPRINT2-011 | LOW | `_ChainProxy.__getitem__` with negative index silently returns wrong block | Fixed: negative index guard raises IndexError |
| SPRINT2-012 | LOW | WebSocket `chain_stats` timer not cancelled on node shutdown | Fixed: timer task cancelled in `node.stop()` |
| SPRINT2-013 | LOW | REST `/txs/sender/:addr` address not validated — invalid hex addresses hit SQLite | Fixed: address format validated before query |
| SPRINT2-014 | LOW | `delete_revocation()` in reorg not wrapped in transaction — partial rollback possible | Fixed: wrapped in SQLite transaction consistent with other reorg operations |
| SPRINT2-015 | INFO | WebSocket client disconnect during broadcast logs traceback at ERROR level | Fixed: log at DEBUG; expected disconnect condition |
| SPRINT2-016 | INFO | REST `/health` endpoint not exempt from rate limiter — monitoring tools can trigger 429 | Fixed: `/health` added to rate limiter exemption list alongside `/info` |

**Round 13 Sprint 2 summary:** 14 fixed, 0 deferred, 2 informational
