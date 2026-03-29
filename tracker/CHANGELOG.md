# Changelog

## Post-v0.8.0: CEO Audit Round 25 (2026-03-29)

### Security Audit
- **Round 25**: Full codebase audit — 9 findings (3 MEDIUM, 4 LOW, 2 INFO)
- R25-001: WebSocket unauthenticated subscriptions (MEDIUM) — assigned
- R25-002: REST auth pattern fragile (MEDIUM) — assigned
- R25-003: PoA skip-slot clock manipulation (MEDIUM) — assigned
- R25-004 to R25-009: Low/info findings documented

### Bug Fixes
- **Fix 2 SSRF test failures**: Replace deprecated `asyncio.get_event_loop().run_until_complete()` with `asyncio.run()` in test_sprint3_openapi_sdk_webhooks.py

### Testing
- Test suite expanded to 2,125 tests (was 1,781)
- All tests pass after fix (99.86% → 100%)

### Tracker
- Updated AUDIT_LOG.md with Round 25 findings
- Created 7 Paperclip subtasks for assigned work
- Created comprehensive plan document (TEC-634)

## v0.8.0 Sprint 1: Multi-Asset Tokens (2026-03-28)

### New Features
- **3 new TX types**: ISSUE_TOKEN, MINT_TOKEN, TRANSFER_TOKEN (14 total)
- **Token registry**: Create custom tokens with name, symbol (unique), decimals, max supply, transferability
- **Token ID**: Deterministic SHA3-256 derivation (issuer + symbol + nonce)
- **Issuer-only minting**: Only the token creator can mint new units
- **Supply cap enforcement**: Integer arithmetic, no overflow possible
- **Non-transferable tokens**: Optional flag to lock transfers
- **State trie integration**: Token balances included in stateRoot
- **Receipt events**: TokenIssued, TokenMinted, TokenTransferred
- **Rollback support**: Full token state reversal on chain reorg
- **SQLite persistence**: tokens + token_balances tables with indexes

### API
- **REST (public)**: GET /tokens, /tokens/{id}, /tokens/{id}/holders, /address/{addr}/tokens
- **REST (protected)**: POST /issue-token, /mint-token, /transfer-token
- **JSON-RPC (public)**: qv_getTokenInfo, qv_getTokenBalance, qv_listTokens, qv_getAddressTokens
- **JSON-RPC (protected)**: qv_issueToken, qv_mintToken, qv_transferToken
- **WebSocket events**: TokenIssued, TokenMinted, TokenTransferred

### Config
- Protocol version: 3 → 4
- Version: 0.7.0 → 0.8.0
- ISSUE_TOKEN fee: 0.5 QBIT, MINT_TOKEN: 0.01 QBIT, TRANSFER_TOKEN: 0.001 QBIT
- TOKEN_ACTIVATION_HEIGHT = 0

### Security (Round 20)
- R20-001 (MED): Webhook DNS rebinding SSRF — delivery-time IP validation
- R20-002 (LOW): Slashing events memory pruning
- R20-003 (INFO): PROTOCOL.md STAKE spec corrected

### Tests
- 1632 tests (+125 new token tests), 0 failures

## v0.8.0 Sprint 2: Light Client Protocol (2026-03-28)

### New Features
- **Block headers API**: `Block.to_header_dict()` — header-only response (no transactions) for light clients
- **Block headers range**: `get_block_headers(start, count)` — paginated headers (max 100)
- **Receipt proofs**: `receipt_proof()` + `verify_receipt_proof()` — Merkle inclusion proof against receiptsRoot
- **Historical state proofs**: `get_state_proof_at_block(key, block_index)` — proof at specific block via state snapshots
- **StateTrie.restore()** — rebuild trie from snapshot for historical proofs

### API
- **REST (public)**: GET /headers, /headers/{index}, /proofs/state/{key}?block=N, /proofs/receipt/{txid}
- **JSON-RPC (public)**: qv_getBlockHeaders, qv_getStateProofAt, qv_getReceiptProof

### Tests
- 1711 tests (+79 light client tests), 0 failures

## v0.8.0 Sprint 3: Binary P2P Protocol (2026-03-28)

### New Features
- **MessageCodec** (`codec.py`): JSON and msgpack wire format backends
- **Length-prefixed binary framing**: 4-byte big-endian length + msgpack payload
- **Binary field optimization**: signature, pubkeys, challenges transmitted as raw bytes (~40-60% bandwidth reduction)
- **Protocol negotiation**: `wire_format` field in hello_auth/auth_response, both v4+ required for msgpack
- **Per-peer wire format tracking**: `Peer.wire_format` attribute in `__slots__`
- **Backward compatible**: v3 peers fall back to JSON transparently
- **Wire format switch after channel setup**: handshake always JSON, msgpack only for post-auth messages

### Tests
- 1781 tests (+70 codec tests), 0 failures

## v0.8.0 Sprint 4: Integration, Audit Round 21, Release (2026-03-28)

### Security (Round 21 — 11 issues)
- **R21-001 (HIGH)**: SQLite tokens/token_balances not cleaned in `delete_blocks_from` — phantom tokens after reorg
- **R21-002 (HIGH)**: ISSUE_TOKEN symbol uniqueness not checked at pool admission
- **R21-003 (HIGH)**: MINT_TOKEN/TRANSFER_TOKEN no state validation at pool admission
- **R21-004 (MED)**: token_id collision check added before registration
- **R21-005 (MED)**: RPC `qv_listTokens` page/limit validation (1-100)
- **R21-006 (MED)**: MINT overflow protection (`_MAX_TOKEN_AMOUNT = 2^63-1`)
- **R21-007 (MED)**: P2P msgpack zero-length frame rejection
- **R21-008 (MED)**: Token rollback negative balance warning log
- **R21-009 (LOW)**: Cached MessageCodec on Peer for msgpack mode
- **R21-010 (LOW)**: Accepted — `get_state_proof_at_block` ambiguous None (v0.9.0)
- **R21-011 (LOW)**: RPC `_rpc_issue_token` transferable type validation

### Docs
- PROTOCOL.md v4: token TX specs, light client section, binary P2P section, weight table
- All agent definitions updated for v0.8.0
- README badges, TX count, audit count updated

### Tests
- 1781 tests, 0 failures, 22 audit rounds

---

## Refactor: blockchain.py mixin extraction (2026-03-26)

- Extracted `PersistenceMixin` to `qbit_network/core/persistence.py` (301 lines): `save()`, `load()`, `_load_from_sqlite()`
- Extracted `RollbackMixin` to `qbit_network/core/rollback.py` (347 lines): `_rollback_to()`, `_rollback_to_inner()`, `_rollback_block()`, `_find_validator_pk_in_chain()`, `_evaluate_fork()`, `_get_blocks_range()`
- `blockchain.py` reduced from 1736 to 1128 lines (-35%)
- Zero logic changes, all 1507 tests passing
- Rollback mixin uses lazy `from . import blockchain as _bc_mod` to read monkeypatched constants (`EPOCH_LENGTH`, `DYNAMIC_FEE_ACTIVATION_HEIGHT`)

---

## Round 19 Audit Fixes (2026-03-26)

### Critical / High
- **R19-PROTO-003/SEC-004**: State root and receipts root mismatches now reject blocks (was only a warning)
- **R19-PERF-001**: Cached computed state root in `_append_block_inner` -- `produce_block` reuses it instead of calling `root()` twice
- **R19-PERF-002**: `StateTrie.root()` now uses dirty-flag caching -- O(1) when state unchanged
- **R19-PERF-003**: Receipt SQLite persistence batched -- single commit per block instead of N+1

### Medium
- **R19-SEC-001**: State trie rebuilt after `_load_from_sqlite()` so trie is populated on node restart
- **R19-SEC-002**: Events and receipts indices rebuilt from SQLite on load
- **R19-SEC-005**: `_block_level_events` pruned beyond `MAX_REORG_DEPTH` to prevent unbounded memory
- **R19-DOC**: Fixed fee tables (PROTOCOL.md, ARCHITECTURE.md), genesis allocation (2.1M not 20M), block reward qubits (5B not 500M), env var prefix (`QBIT_` not `QVAULT_`), audit round count (19)

### Stats
- Total audit rounds: 19
- Issues found: 13, fixed: 13

---

## v0.7.0: State Proofs, Receipts, SDK, Webhooks, Finality (2026-03-26)

### Summary

QBit Network v0.7.0 adds Merkle state proofs (`stateRoot` + `receiptsRoot` in block headers), a transaction receipt and event system, simple finality (>2/3 stake), a webhook event delivery system with HMAC-SHA256 signatures, a zero-dependency Python SDK, and an OpenAPI 3.0 specification. Four sprints, 150 new tests (1507 total), 18 audit rounds with 202+ issues found and fixed.

### Round 18 Security Audit (Sprint 4)

5 issues found, 4 fixed, 1 accepted:
- **R18-001 (HIGH)**: Webhook SSRF -- private/loopback/metadata URL blocking via `ipaddress` validation
- **R18-002 (HIGH)**: SDK query parameter injection -- proper URL encoding via `urllib.parse.urlencode()`
- **R18-003 (MED)**: State snapshot memory growth -- pruning beyond `MAX_REORG_DEPTH`
- **R18-004 (MED)**: REST `/events` endpoint limit bypass -- validated to 1-100 range
- **R18-005 (LOW)**: Webhook delivery task accumulation -- accepted (bounded by design)

### Version
- VERSION = `0.7.0`
- Total tests: 1507, all passing
- Total audit rounds: 18
- Total issues found and fixed: 202+

---

## v0.7.0-sprint3: OpenAPI Spec + Python SDK + Webhooks (2026-03-26)

### OpenAPI 3.0 Specification
- **`docs/openapi.yaml`**: Complete REST API spec covering all 35+ endpoints. Includes schemas for Block, Transaction, Wallet, Receipt, Event, BalanceInfo, SupplyInfo, FeeInfo, NodeInfo, StateProof, Webhook, and WebhookRegistration. Defines BearerAuth security scheme, pagination parameters, error response format, and configurable server URLs.

### Python SDK
- **`sdk/qbit_sdk/`**: pip-installable Python SDK with zero external dependencies (stdlib `urllib.request` only).
- **`QBitClient`**: Synchronous HTTP client with all public endpoints (get_info, get_block, get_balance, get_supply, get_fee_info, get_validators, get_state_proof, verify_document, get_receipt, get_finalized_height, get_events) and protected endpoints (create_wallet, list_wallets, transfer, notarize, store, share, stake, delegate, unstake, register_validator).
- **Data models**: `Block`, `Transaction`, `Wallet`, `NodeInfo`, `BalanceInfo`, `SupplyInfo`, `FeeInfo`, `StateProof`, `Receipt`, `Event`, `VerifyResult`, `Webhook` -- all with `from_dict()` classmethods.
- **Exceptions**: `QBitError` (base), `AuthenticationError` (401), `NotFoundError` (404), `InsufficientBalance` (400), `ValidationError` (400).
- **WebSocket client** (`qbit_sdk/websocket.py`): `QBitWebSocket` with channel subscriptions, callback dispatch, background thread support.
- **Webhook management**: `register_webhook()`, `list_webhooks()`, `delete_webhook()`.

### Webhook System
- **`qbit_network/network/webhooks.py`**: `WebhookManager` with registration, event filtering, async delivery, and lifecycle management.
- **Registration**: URL validation (http/https required), event type validation (13 valid types matching all TX event types + block-level events), HMAC secret validation. Max 100 webhooks per node.
- **Delivery**: HTTP POST to registered URL with JSON body `{event, block_index, timestamp}`. `X-QBit-Signature` header: HMAC-SHA256 of body using registered secret. `X-QBit-Webhook-Id` header for correlation.
- **Retry policy**: 3 attempts with exponential backoff (1s, 5s, 25s). 10s timeout per delivery attempt. Webhook marked `failing` after first delivery failure, `disabled` after 10 consecutive failures.
- **RPC methods**: `qv_registerWebhook(url, events, secret)` (protected), `qv_listWebhooks()` (protected), `qv_deleteWebhook(webhook_id)` (protected).
- **REST endpoints**: `POST /api/v1/webhooks`, `GET /api/v1/webhooks`, `DELETE /api/v1/webhooks/{id}` -- all protected with Bearer auth.
- **Node integration**: Webhook delivery triggered after block processing in `_ws_notify_block`. Events collected from TX receipts and block-level events.

### Tests
- 65 new tests in `tests/test_sprint3_openapi_sdk_webhooks.py`: 10 OpenAPI spec validation, 9 SDK model deserialization, 4 SDK exception hierarchy, 16 SDK client methods (mocked HTTP), 13 webhook registration/management, 3 HMAC signature, 8 webhook delivery (retry, failure, disable), 1 event type validation, 1 HMAC in delivery.

### Version
- VERSION bumped to `0.7.0-sprint3`.
- Total tests: 1507 (1442 existing + 65 new), all passing.

## v0.7.0-sprint2: Receipt/Event System + Simple Finality (2026-03-26)

### Receipt System
- **`TransactionReceipt`** (`qbit_network/core/receipt.py`): immutable record of TX execution result with `__slots__` (tx_id, status, fee_paid, block_index, tx_index, events). SHA3-256 `receipt_hash` property for Merkle tree. `to_dict`/`from_dict` with full input validation.
- **`receipts_root()`**: Merkle root of receipt hashes using the same SHA3-256 binary tree as transaction Merkle root.
- **`build_event()`**: typed event constructor for all TX types.

### Event Types
- All 11 TX types emit structured events: Transfer, Notarize, Store, Share, Stake, Delegate, Unstake, KeyRegistered, ValidatorRegistered, KeyRevoked, Slashed.
- Block-level events: BlockReward (validator, amount), EpochTransition (epoch).

### Block Header
- **`receipts_root`** field added to Block `__slots__`, `__init__`, `_header_bytes`, `to_dict`, `from_dict`. Included in block hash only when non-empty (same backward-compatible activation gate as stateRoot).

### Blockchain Integration
- Receipts generated inside `_append_block_inner` alongside TX processing. Each TX's fee_paid tracked and events built per TX type.
- Receipt rollback in `_rollback_block`: receipts, event indices, and block-level events cleaned up.
- `get_receipt(tx_id)`: look up receipt by TX ID (in-memory + SQLite fallback).
- `get_events(event_type, block_index, sender, limit)`: filtered event query.
- `get_block_level_events(block_index)`: BlockReward and EpochTransition events.

### Simple Finality
- **`_update_finality()`**: walks backward from latest block, accumulates unique validator stake. Block finalized when >2/3 of total stake represented.
- **`get_finalized_height()`**: returns latest finalized block index (-1 if none).
- Finality reset on rollback past finalized height.
- Only active when financial layer is enabled and total stake > 0.

### SQLite Persistence
- New tables: `receipts` (tx_id PK, status, fee_paid, block_index, tx_index, events_json) and `events` (id PK, block_index, tx_id, event_type, event_data).
- Indices: `idx_receipts_block`, `idx_events_type`, `idx_events_block`.
- `put_receipt()`, `get_receipt()`, `get_receipts_for_block()`, `delete_receipts_for_block()`, `query_events()` methods on SQLiteStore.
- Cleanup in `delete_blocks_from()` for rollback.

### RPC
- **`qv_getReceipt(tx_id)`** -- public, returns receipt dict or null.
- **`qv_getFinalized()`** -- public, returns `{finalized_height: int}`.
- **`qv_getLogs(event_type, block_index, sender, limit)`** -- public, returns filtered event list.

### REST API
- **`GET /api/v1/receipt/{txid}`** -- receipt with events.
- **`GET /api/v1/events?type=...&from=...&limit=...`** -- event query with pagination.
- **`GET /api/v1/finalized`** -- current finalized height.

### WebSocket
- **`finalized`** channel added to `VALID_CHANNELS`. Emits `{finalized_height: int}` when finalized height changes after a new block.

### Tests
- 48 new tests in `tests/test_receipts_events.py`: receipt CRUD, all event types, receiptsRoot in block header, SQLite persistence, event filtering, finality advance/rollback, block-level events, RPC methods, WS channel.

### Version
- VERSION bumped to `0.7.0-sprint2`.

## v0.7.0-sprint1: State Root in Block Header (2026-03-26)

### State Tree
- **StateTrie** (`qbit_network/core/state_tree.py`): sorted key-value Merkle trie using SHA3-256 with domain-separated hashing. Covers `balance:{address}` and `nonce:{address}` entries. Supports proof generation, static verification, snapshot/restore for rollback.

### Block Header
- **`state_root`** field added to Block `__slots__`, `__init__`, `_header_bytes`, `to_dict`, `from_dict`. Included in block hash computation only when non-empty, preserving backward compatibility with pre-existing blocks (state_root="").
- **`receipts_root`** field also handled with same backward-compatible pattern.

### Blockchain Integration
- State trie rebuilt from `_balances` and `_sender_nonce` after every block append (`_rebuild_state_trie()`).
- Self-produced blocks (genesis via `init_chain`, subsequent via `produce_block`) are stamped with `state_root` and re-signed before storage.
- Received blocks verified: computed state root logged as warning if mismatch (consensus is the gatekeeper).
- Trie snapshots saved per block index for O(1) rollback via `_rollback_block`.
- `activate_financial_layer()` rebuilds trie and updates snapshot after genesis balance allocation.

### RPC / REST API
- `qv_getStateProof(address, key_type)` — public JSON-RPC method returning Merkle inclusion proof.
- `qv_getStateRoot()` — public JSON-RPC method returning current state root hex.
- `GET /api/v1/state-proof/{addr}?key=balance|nonce` — REST endpoint for state proofs.
- `GET /api/v1/state-root` — REST endpoint for current state root.

### Proof System
- `export_proof()` and `verify_proof()` in `qbit_network/core/proof.py` updated to include `stateRoot` and `receiptsRoot` in block header reconstruction.
- CLI `verify-proof` command updated for backward-compatible header hash verification.

### Storage
- `SQLiteStore.update_block()` added for re-signing blocks after state root stamping.

### Tests
- 37 new tests in `tests/test_state_trie.py`: trie basics, snapshots, proof generation/verification, blockchain integration, state proof RPC, adversarial inputs.
- All 1442 existing + new tests passing.

## v0.6.0 Round 17 Security Audit Fixes (2026-03-26)

### Security (Round 17 Audit)
- **[CRITICAL] R17-001: Auth bypass in HELLO_AUTH** — initiator proof could be bypassed under specific timing conditions, allowing an attacker to complete the handshake without proving ownership of the claimed signing key. Fixed: initiator proof (`Sign(sk, AUTH_DOMAIN || challenge || address)`) is verified by the responder before any signing occurs; absent or invalid proof triggers immediate disconnect.
- **[MEDIUM] R17-002: Unbonding persistence gap** — `_process_mature_unbondings()` was not called on chain reload from SQLite, causing mature unbondings to be skipped after a node restart. Fixed: mature unbondings processed during `_load_from_sqlite()` after full chain replay.

### Version
- PROTOCOL_VERSION remains 3 (no wire format change).

## v0.6.0-sprint2+3: EIP-1559 API, NextJS, Docs, Audit (2026-03-26)

### API
- **`GET /api/v1/fee`** -- new public REST endpoint returning current base_fee, next_base_fee,
  suggested_priority_fee, TX weight table, and per-type estimated fees.
- **`qv_getFeeInfo()`** -- new public JSON-RPC method returning same fee info.
- **TX responses** now include `effectiveFee` for confirmed transactions in dynamic fee blocks.
- **Block responses** already include `baseFee` (added in Sprint 1).
- **Supply response** now includes `fee_model: "dynamic"|"fixed"`.

### NextJS Dashboard
- **`FeeInfo` type** added to `lib/types.ts` for type-safe fee data.
- **`getFeeInfo()`** method added to `QBitAPI` client.
- **`estimateFee()`** helper in `lib/format.ts` for fee estimation.
- **TransferForm** -- fetches dynamic fee info on mount, shows base fee and estimated fee,
  priority fee slider, and dynamic total cost. Falls back to fixed fee display when dynamic
  fees are not active.
- **StatsBar** -- added "Base Fee" stat with current base_fee from `/fee` endpoint.
- **BlockDetail** -- shows `baseFee` in block information panel when non-zero.
- **TxDetail** -- shows `maxFeePerWeight`, `maxPriorityFee`, `effectiveFee` in new
  "Dynamic Fee (EIP-1559)" card when fields are present.

### Documentation
- **PROTOCOL.md** -- added EIP-1559 fee section with formula, base fee adjustment algorithm,
  weight table, and anti-spam rules.
- **ARCHITECTURE.md** -- replaced fixed fee section with dual-model (legacy + EIP-1559) section.
- **README.md** -- updated fee description to mention EIP-1559 dynamic fees.

### Tests
- 6 new REST API tests for `/fee` endpoint (public access, required fields, types).
- 7 new EIP-1559 integration tests (RPC fee info, estimated fees, fee adjustment under load).
- Total: **1358 tests passing** (was 1347).

## v0.6.0-sprint1: EIP-1559 Dynamic Fee Engine (2026-03-26)

### Features
- **EIP-1559 dynamic fee mechanism** -- base fee adjusts +/-12.5% per block based on
  effective block weight utilization vs 50% target. All fees (100%) credited to validator.
- **New module `qbit_network/core/fees.py`** -- pure fee calculation functions:
  `compute_base_fee()`, `compute_tx_fee()`, `effective_block_weight()`, `tx_weight()`.
- **Block header: `base_fee` field** -- included in header hash (hard fork). Serialized
  as `baseFee` in JSON. Genesis blocks have base_fee=0.
- **Transaction fee fields** -- `max_fee_per_weight` and `max_priority_fee` added to
  Transaction. Included in signable bytes (hard fork: changes tx_id). All 11 factory
  classmethods accept fee parameters.
- **Consensus validation** -- validates base_fee derivation from parent, block weight
  limit (MAX_BLOCK_WEIGHT=20M), self-TX ratio cap (25%), per-TX fee sufficiency.
  Empty blocks allowed post-activation.
- **Pool admission** -- rejects TXs below current base_fee. Balance check uses
  worst-case fee (max_fee_per_weight * weight).
- **Block production** -- sorts senders by descending priority fee while preserving
  per-sender nonce order. Respects MAX_BLOCK_WEIGHT. Produces empty blocks when pool empty.
- **Rollback** -- reverses dynamic fees (100% from validator). base_fee restored from parent.
- **Hard fork activation** -- `DYNAMIC_FEE_ACTIVATION_HEIGHT` config constant. Pre-activation
  blocks use legacy fixed fee schedule (50% burn). Default is high (inactive) for backward
  compatibility; set to 0 for new chains.

### Config
- `PROTOCOL_VERSION` bumped to 3
- Added: `TX_WEIGHTS`, `MAX_BLOCK_WEIGHT`, `TARGET_BLOCK_WEIGHT`, `BASE_FEE_CHANGE_DENOM`,
  `INITIAL_BASE_FEE`, `MIN_BASE_FEE`, `MAX_BASE_FEE`, `DYNAMIC_FEE_ACTIVATION_HEIGHT`,
  `MAX_SELF_TX_WEIGHT_RATIO`

### Tests
- **83 new tests** in `tests/test_eip1559.py` covering fee engine, block/TX fields,
  consensus validation, blockchain integration, pool admission, block production,
  rollback, self-TX anti-spam, base fee adjustment, legacy path.
- Total: **1347 tests passing** (was 1264).

## v0.5.0-sprint4: Financial Layer Security Audit + Adversarial Tests (2026-03-26)

### Security (Round 16 Audit)
- **[CRITICAL] R16-003: Epoch reward distribution supply inflation** -- `_distribute_epoch_rewards`
  credited delegators without debiting validators, creating tokens from nothing. Fixed: validator
  balance debited by total distributed amount; rollback records include explicit debit/credit
  entries for clean reversal.
- **[MEDIUM] R16-001: TRANSFER recipient address not validated** -- funds could be sent to
  arbitrary string addresses (non-qv1) that are permanently unrecoverable. Fixed:
  `validate_payload` checks `qv1` prefix, 67-char length, hex-only suffix.
- **[MEDIUM] R16-005: Rollback block reward partial reversal** -- when validator spent reward
  before rollback, `_total_minted` decremented by full reward but balance only partially debited.
  Accepted: defense-in-depth (prevents negative balance); full rebuild on SQLite load resolves.
- **[LOW] R16-002: `_pending_debits()` O(n) pool scan** -- bounded by MAX_TX_POOL_SIZE (10,000).
  Accepted for now; precomputed dict optimization tracked for future.
- **[LOW] R16-004: Negative circulating supply** -- transient condition; no user-facing impact.

### Tests
- **59 new adversarial tests** in `tests/test_financial_adversarial.py` covering:
  - Double-spend (pool + replay), overflow/underflow, zero/negative amounts
  - Self-transfer, fee evasion verification, MINT forgery prevention
  - Halving boundary, supply cap, rollback balance/burn/minted consistency
  - Concurrent TRANSFER + STAKE, circular A->B->C->A transfers
  - Fee-free types (REVOKE_KEY, EVIDENCE), memo length limits
  - Invalid recipient formats (non-qv1, wrong length, non-hex)
  - Pending debits accuracy, genesis balance idempotency
  - Fee burn percentage, nonce ordering, integer arithmetic
- Total: **1264 tests passing** (was 1205).

### Documentation
- `docs/ARCHITECTURE.md` -- added Financial Layer section (token economics, fees, rewards, epoch distribution)
- `docs/PROTOCOL.md` -- added TRANSFER format, fee schedule, block reward specification, TRANSFER rules
- `docs/SECURITY.md` -- added Round 16 findings and financial layer security controls
- `tracker/AUDIT_LOG.md` -- added Round 16 (5 issues: 3 fixed, 2 accepted)
- `tracker/ISSUES.md` -- updated summary (16 rounds, 1264 tests)
- `tracker/CHANGELOG.md` -- this entry
- `README.md` -- updated for financial features, 11 tx types, 16 audit rounds

## v0.5.0-sprint2: Staking Migration + Epoch Rewards + Supply Tracking (2026-03-26)

### New Features
- **Epoch Reward Distribution**: Block rewards accumulated per-validator during each epoch
  are distributed to delegators proportionally at epoch boundaries. Validator keeps
  commission (default 10%), remaining pool split by delegation weight. Integer arithmetic
  only -- no rounding exploits beyond 1 base unit.
- **Validator Commission**: Validators can set commission rate (0-100%) via `"commission"`
  field in REGISTER_VALIDATOR payload. Default is 10%. Queryable via
  `get_validator_commission()`.
- **Enhanced Supply Tracking**: `get_total_supply()` now includes `staked` (sum of all
  locked stakes), `circulating` (minted - burned - staked), and `max_supply` (1B QBIT).
  Conservation invariant: `circulating + staked + burned == total_minted`.
- **Epoch Reward Queries**: `get_epoch_rewards(validator_addr)` returns accumulated
  rewards for current epoch.

### Technical
- `_epoch_rewards: dict[str, int]` tracks per-validator accumulated block rewards.
- `_validator_commission: dict[str, int]` stores commission rates.
- `_last_epoch_distributions: dict[int, list[tuple[str, int]]]` enables rollback of
  epoch distributions.
- `_distribute_epoch_rewards()` called at epoch boundary before validator snapshot.
- Epoch distribution persisted via extended `_persist_balances_after_block()` that
  includes delegator addresses at epoch boundaries.
- `DEFAULT_COMMISSION_RATE = 10` added to config.
- `"commission"` added to REGISTER_VALIDATOR allowed payload keys with validation.
- Balance persistence moved after epoch transition for correct ordering.
- 35 new tests covering all features, rollback, and adversarial inputs.
- Total: 1205 tests passing (was 1170).

## v0.5.0-sprint3: NextJS Financial UI Updates (2026-03-26)

### New Features
- **QBIT Formatting**: `formatQBIT()` / `parseQBIT()` utilities in `lib/format.ts` for
  converting between raw qubits (integer) and human-readable QBIT strings (10^8 decimals).
- **Balance Display**: WalletCard now shows wallet balance prominently with staked amount
  and a "Send QBIT" action button linking to the transfer page.
- **Transfer Page**: New `/transfer` route with wallet dropdown, recipient input, decimal
  amount entry, optional memo (256 char limit), network fee display, and confirmation
  dialog before submission. Success/error feedback with toast notifications.
- **Supply Widget**: Dashboard supply overview showing minted/max progress bar and
  circulating/burned/staked breakdown cards.
- **StatsBar Supply**: Circulating supply stat added to the stats bar on all pages.
- **Block Rewards**: BlockDetail now shows the block reward amount for COINBASE/REWARD txs.
- **Transfer Details**: TxDetail shows amount, memo, and fee prominently for TRANSFER type.
  All transaction types show fee when present in payload.
- **API Client**: Added `getBalance()`, `getSupply()`, `transfer()` methods to `QBitAPI`.
- **Types**: Added `BalanceInfo` and `SupplyInfo` TypeScript interfaces.
- **Badge Variants**: TRANSFER (emerald) and COINBASE (yellow) type colors.
- **Sidebar**: Transfer navigation link added between Wallets and Notarize.

### Technical
- TransferForm extracted to separate component with Suspense boundary for `useSearchParams`
  SSR compatibility (Next.js 16 requirement).
- No `dangerouslySetInnerHTML` usage. All user content rendered via React text nodes.
- Responsive layout maintained across all new components.
- Build passes with zero TypeScript errors on Next.js 16.2.1 + Turbopack.

## v0.5.0-sprint1: Core Balance Ledger + TRANSFER + Fees (2026-03-26)

### New Features
- **Balance Ledger**: Unified balance tracking with `_credit()` / `_debit()` as sole
  mutation primitives. Integer arithmetic only. Sequential intra-block validation.
- **TRANSFER Transaction**: New `TxType.TRANSFER` for token transfers between addresses.
  Factory method, payload validation (amount, memo, recipient), balance checks.
- **Transaction Fees**: Per-type fee schedule (`TX_FEES` config). 50% burned, 50% to
  block validator. Enforced sequentially in `_append_block_inner()`.
- **Block Rewards**: Implicit MINT (not a user tx). Initial 5 QBIT/block with halving
  every 2,100,000 blocks. Supply capped at 1B QBIT.
- **Genesis Balance**: 20M QBIT allocated to genesis validator via
  `activate_financial_layer()`.
- **Token Economics**: `TOKEN_NAME`, `TOKEN_SYMBOL`, `TOKEN_DECIMALS`, `QUBIT_PER_QBIT`,
  `MAX_SUPPLY`, fee schedule, halving parameters.
- **SQLite Persistence**: `balances` and `supply` tables for balance state persistence.
- **Mature Unbonding Credits**: Unbonded stake credited back to balance on maturity.
- **STAKE/DELEGATE Balance Deduction**: Staked amounts debited from sender balance.
- **RPC**: `qv_getBalance`, `qv_transfer`, `qv_getSupply`.
- **REST API**: `GET /balance/:addr`, `POST /transfer`, `GET /supply`.
  `GET /address/:addr` now includes `balance` field.
- **Rollback**: Full balance reversal on block rollback (fees, transfers, rewards).

### Backward Compatibility
- Financial layer is opt-in via `activate_financial_layer()`. Existing chains without
  genesis balance allocation continue to work without fee enforcement.
- All 1107 existing tests pass unchanged. 63 new financial tests added.

### Version Bump
- `VERSION` bumped from `0.4.0` to `0.5.0`.

## Round 15 Security Audit Fixes (2026-03-25)

### MEDIUM Fixes
- **R15-001**: Evidence verification now uses raw header bytes instead of block hash.
  EVIDENCE payloads include `block_a_header` / `block_b_header` (hex-encoded header
  JSON). Signatures verified against headers; headers verified to hash to claimed
  block hashes. Updated `Transaction.evidence()` factory, `validate_payload()`, and
  `_process_evidence_tx()`. Removed broken `_build_evidence_header()`.
- **R15-002**: P2P signing and encryption secret keys zeroed on `P2PNode.stop()` via
  `SecureBytes.zero()` (if available).
- **R15-003**: Added explanatory comment on `threading.Lock` usage in `Blockchain`
  (intentional: SQLite ops are synchronous and sub-ms; asyncio.Lock not warranted).
- **R15-004**: `_wallet_locks` in `FullNode` now uses bounded `OrderedDict` (cap
  10,000 entries, LRU eviction) to prevent unbounded memory growth.
- **R15-005**: `/api/v1/blocks` list endpoint now returns block headers only
  (transactions omitted). Full tx data available via `/api/v1/blocks/{index}`.

### LOW Fixes
- **R15-006**: `SecureBytes.__eq__` now uses `hmac.compare_digest()` for
  constant-time comparison, preventing timing side-channels.
- **R15-007**: Replaced deprecated `datetime.utcnow()` with
  `datetime.now(datetime.timezone.utc)` in TLS cert generation.
- **R15-008**: `documentHash` payload validation now enforces max 128 hex chars
  for NOTARIZE and STORE transactions.
- **R15-009**: `export_proof()` now includes `chain_id` field. `verify_proof()`
  checks chain_id matches if present.

## v0.4.0-sprint3 (2026-03-25)

### TLS Auto-Provisioning (ISS-016)
- New `TLSManager` class in `qbit_network/network/tls_manager.py`
- Auto-generates self-signed ECC (P-256) certificates with proper X.509 fields
- Certificate CN set to configured hostname; SAN includes hostname, localhost, 127.0.0.1, ::1
- BasicConstraints(ca=False) extension for correct self-signed cert behavior
- Auto-renewal: checks cert expiry against configurable threshold (default 30 days)
- External cert hot-reload: watches file modification times, reloads SSL context on change
- SIGHUP signal handler triggers manual TLS context reload (Unix platforms)
- Atomic file writes for cert and key using tempfile + os.replace
- Key files written with 0o600 permissions
- New CLI flags: `--tls-auto` (auto-manage certs), `--tls-hostname` (cert CN/SAN)
- `--tls-self-signed` preserved as backward-compatible alias for `--tls-auto`
- Background async watcher task for periodic cert change detection
- Config constants: `TLS_CERT_VALIDITY_DAYS`, `TLS_RENEWAL_THRESHOLD_DAYS`

### Secure Key Material Zeroing (ISS-001)
- New `SecureBytes` class in `qbit_network/crypto/secure_bytes.py`
- ctypes-backed mutable byte buffer with explicit `zero()` to scrub key material
- Full bytes-like interface: `__bytes__`, `__len__`, `hex()`, `__eq__`, `__hash__`
- Context manager and destructor auto-zero for defense in depth
- `Wallet.__init__` wraps `signing_sk` / `encryption_sk` in SecureBytes
- `Wallet.close()` and `Wallet.__exit__` zero all secret keys
- `MLDSA.sign()` and `MLKEM.decapsulate()` transparently accept SecureBytes
- scrypt-derived keys zeroed after wallet encrypt/decrypt operations
- Decrypted plaintext buffer zeroed after key extraction in `_decrypt()`
- `FullNode.stop()` calls `wallet.close()` for all loaded wallets
- Fallback to bytearray with best-effort zeroing if ctypes is unavailable
- 42 new tests covering unit, crypto integration, wallet lifecycle, and GC

### Peer Reputation Scoring (ISS-009)
- New `PeerReputation` class in `qbit_network/network/reputation.py`
- Score-based tracking with 8 event types (valid_block, invalid_block, auth_failed, etc.)
- Peers start at score 100; banned when score drops to -100 or below
- Score decay (0.99x per decay call) ensures old events fade over time
- Integrated into P2P layer: events recorded for block/tx validation results
- Banned peers rejected on inbound connections and disconnected mid-session
- Auth failures, rate limit violations, protocol errors all track reputation
- Manual unban via `unban()` resets score to default

### Chain Pruning (ISS-007)
- New `PRUNING_RETENTION = 10000` configuration parameter
- `Blockchain.prune(retention)` removes block data older than `height - retention`
- `SQLiteStore.prune_blocks(before_index)` performs atomic block+tx row deletion
- All indices preserved: notarizations, key_registry, validator_registry, stakes, epochs, slashing
- Thread-safe via existing `_db_lock`; no-op in in-memory mode

### Block Signature in Proof Verification (R14-006)
- `export_proof()` now accepts optional `validator_pubkey` parameter
- When present, validator's ML-DSA-65 public key included in proof bundle
- `verify_proof()` performs full ML-DSA signature verification on block header
- Tampered signatures and wrong validator pubkeys correctly detected with clear errors
- Backward compatible: proofs without `validator_pubkey` skip signature check

### Dashboard dPoS Updates
- Current Epoch stat chip added to stats bar
- Validators tab shows stake weight and slashed indicator badge per validator
- New Staking panel (6th tab): validator stakes, top stakers, epoch info, slashing events
- Pool Monitor updated with STAKE, DELEGATE, UNSTAKE, EVIDENCE tx type colors
- Dashboard fetches from GET /api/v1/stakes, GET /api/v1/epochs/current, GET /api/v1/slashing-events
- Dashboard size: 45KB (under 60KB limit), XSS-safe DOM manipulation

### Documentation (v0.4.0 Complete)
- ARCHITECTURE.md: dPoS consensus, epoch rotation, slashing, P2P encryption, peer reputation, chain pruning
- PROTOCOL.md: dPoS consensus, STAKE/DELEGATE/UNSTAKE/EVIDENCE tx formats, encrypted channel spec, hello_auth proof field
- SECURITY.md: dPoS security model, slashing, P2P encryption, deferred findings resolved
- PAPER.md: updated abstract, dPoS section, 15 audit rounds, 1080 tests, v0.4.0 final status
- AUDIT_LOG.md: deferred findings SPRINT1-003/007/011 marked resolved in v0.4.0
- ISSUES.md: ISS-007 (pruning) and ISS-009 (reputation) closed
- FEATURES.md: all v0.4.0 features marked implemented, remaining items moved to v0.5.0+
- CHANGELOG.md: complete v0.4.0 changelog

### Tests
- 76 new tests: 34 TLS manager, 42 SecureBytes, 26 reputation, 10 pruning, 10 proof signature
- Total: 1080 tests, all passing

## v0.4.0-sprint2 (2026-03-25)

### Epoch Rotation
- Validator set frozen at every EPOCH_LENGTH (100) block boundary
- Epoch snapshots stored in-memory (`_epochs` dict) and SQLite (`epochs` table)
- `get_current_epoch()` and `get_epoch_validators()` query methods
- Consensus uses frozen epoch validators for dPoS selection during epoch
- Stake changes take effect at next epoch boundary
- Epoch state correctly rolled back during chain reorganization
- RPC: `qv_getEpoch` (public); REST: `GET /api/v1/epochs/current`

### Slashing (Double-Sign Evidence)
- New EVIDENCE transaction type for reporting validator double-signing
- `Transaction.evidence()` factory method with payload validation
- Evidence processing: verifies two valid ML-DSA-65 signatures over different block hashes at same index
- Slashing: reduces all stakers' positions by SLASH_PERCENTAGE (50%) proportionally
- Validator removed from active set if total stake drops below MIN_STAKE
- Slashed validators cannot receive new stake
- Duplicate evidence rejected (one slash per validator)
- SQLite `slashing_events` table for persistent slashing history
- Slashing state correctly rolled back during chain reorganization
- RPC: `qv_submitEvidence` (protected), `qv_getSlashingEvents` (public)
- REST: `POST /api/v1/evidence` (protected), `GET /api/v1/slashing-events` (public)
- EVIDENCE payloads exempt from 8KB limit (32KB limit, accommodates two ML-DSA-65 signatures)
- 37 new tests covering tx validation, epoch rotation, slashing logic, rollback, SQLite persistence

### Auth Verify-Before-Sign Fix (SPRINT1-003)
- Initiator now includes `proof` field in `hello_auth` message
  - Proof = Sign(sk, AUTH_DOMAIN || challenge || initiator_address)
  - Responder verifies proof before signing anything (fixes identity confusion vulnerability)
- Missing, empty, invalid hex, wrong-key, and wrong-challenge proofs all rejected
- 7 new tests for proof validation

### P2P Encrypted Channel
- Post-authentication encrypted transport using ML-KEM-768 + AES-256-GCM
  - Initiator generates ML-KEM encapsulation using responder's `encryption_pk`
  - Sends `session_key` message with ciphertext and own `encryption_pk`
  - Responder decapsulates to recover shared secret
  - Both derive 32-byte AES key via SHA3-256(shared_secret)
- All post-handshake messages wrapped in `{"type": "encrypted", "data": ct_hex}`
- `Peer` gains `session_key`, `encrypted`, `encryption_pk` fields
- `Peer.send_encrypted()` method: AES-GCM wraps if encrypted, plaintext fallback otherwise
- `P2PNode._decrypt_message()` handles inbound encrypted messages in both read loops
- `P2PNode._initiate_encrypted_channel()` called after auth completes (initiator side)
- `P2PNode._handle_session_key()` processes key exchange (responder side)
- `broadcast()` uses `send_encrypted()` for automatic encryption
- Backward compatible: v1 peers and peers without `encryption_pk` stay plaintext
- `P2PNode` constructor accepts `encryption_sk`/`encryption_pk` parameters
- Node passes validator wallet's encryption keys to P2P layer on startup
- 22 new tests for encrypted channel

### Connection Deduplication (A-01)
- After successful authentication, checks for duplicate connections to same remote address
- Deterministic tie-breaker: node with lexicographically smaller address keeps its outbound connection
- `Peer` gains `is_initiator` and `remote_address` fields
- `P2PNode._dedup_connection()` called after auth completes on both sides
- Losing connection is closed immediately; winning connection continues
- 10 new tests for dedup logic

### Full Integration Test
- `TestMutualAuthWithEncryption.test_full_handshake_with_encrypted_channel`
  - Simulates complete auth + encryption flow between two nodes
  - Verifies both sides derive identical session keys

## v0.4.0-sprint1 (2026-03-25)

### Genesis Validator On-Chain Transaction (SPRINT1-007)
- Genesis validator now registered via REGISTER_VALIDATOR tx in genesis block
  - Removes direct \_validator_registry / consensus.add_validator() writes from init_chain()
  - \_append_block processes the genesis REGISTER_VALIDATOR tx uniformly
  - Rollback and chain reload work correctly with the new genesis structure
- Block.genesis() accepts optional transactions parameter
- Genesis block transactions do not consume user-facing nonce slots
  - Nonce tracking skipped for block index 0 in \_append_block_inner and \_load_from_sqlite
  - Rollback nonce recomputation excludes genesis block txs
- Fixed pre-existing dPoS bug: sha3_256() returns bytes, not hashlib object (.digest() removed)

### Delegated Proof of Stake (dPoS) Consensus
- New transaction types: `STAKE`, `DELEGATE`, `UNSTAKE`
  - `STAKE`: self-stake weight on own validator (amount 1 - 1,000,000)
  - `DELEGATE`: delegate stake weight to any registered validator
  - `UNSTAKE`: begin unbonding (effective after 100-block UNBONDING_PERIOD)
  - Factory classmethods: `Transaction.stake()`, `.delegate()`, `.unstake()`
  - Payload validation: integer amount in [MIN_STAKE, MAX_STAKE], non-empty validator_address
  - Allowed keys enforcement prevents dedup bypass
- Stake-weighted validator selection in `consensus.py`
  - Deterministic seed: `SHA3-256(parent_hash:block_index)` for unpredictable selection
  - Weighted random: cumulative distribution over sorted validators by address
  - Automatic PoA round-robin fallback when no validators have stake
  - `_select_dpos()` static method for testability
- Blockchain staking state (`blockchain.py`)
  - `_stakes`: validator_addr -> {staker_addr: amount}
  - `_total_stake`: validator_addr -> total stake weight
  - `_unbonding`: list of pending unbonding entries with release_block
  - Query methods: `get_validator_stake()`, `get_staker_info()`, `get_active_validators()`, `get_all_stakes()`
  - STAKE/DELEGATE processing in `_append_block_inner`
  - UNSTAKE processing with unbonding entry creation
  - Mature unbonding cleanup in `_process_mature_unbondings()`
  - Full rollback support in `_rollback_block` for all staking tx types
  - State rebuild in `_load_from_sqlite` for chain reload
  - submit_tx validation: registered validator check, insufficient stake check
- SQLite persistence (`store.py`)
  - New tables: `stakes (staker, validator, amount)`, `unbonding (staker, validator, amount, release_block)`
  - Methods: `put_stake()`, `get_stake()`, `delete_stake()`, `get_all_stakes()`
  - Methods: `put_unbonding()`, `get_mature_unbondings()`, `delete_unbonding()`
  - `delete_blocks_from()` rebuilds stake state from remaining chain on rollback
- JSON-RPC endpoints (`node.py`)
  - Protected: `qv_stake`, `qv_delegate`, `qv_unstake`
  - Public: `qv_getStake`, `qv_getValidatorStakes`
- REST API endpoints (`rest_api.py`)
  - `GET /api/v1/stakes` — all validator stakes
  - `GET /api/v1/stakes/:validator` — specific validator stake info
  - `POST /api/v1/stake` — stake (protected)
  - `POST /api/v1/delegate` — delegate (protected)
  - `POST /api/v1/unstake` — unstake (protected)
- Configuration (`config.py`)
  - `MIN_STAKE = 1`, `MAX_STAKE = 1_000_000`
  - `UNBONDING_PERIOD = 100` blocks
  - `EPOCH_LENGTH = 100` blocks (placeholder for Sprint 2)
  - Version bumped to 0.4.0
- 58 new tests in `tests/test_dpos.py`
  - Transaction factory and serialization roundtrip tests
  - Payload validation (bounds, types, missing fields, extra keys)
  - Staking state management (stake, delegate, unstake, accumulation)
  - Consensus selection (PoA fallback, determinism, weighted distribution)
  - Rollback correctness (stake, delegate, unstake reversal)
  - Unbonding period mechanics
  - SQLite store unit tests
  - Integration tests (block production, backward compatibility)
  - Edge cases (zero amount, float amount, self-delegation, combined stakes)

## v0.3.0-sprint3 (2026-03-25)

### IPFS Integration for CLI (store/share/retrieve)
- New `cli/ipfs_client.py` — stdlib-only IPFS HTTP API client (`urllib.request`)
  - `add_file(path)` / `add_bytes(data, name)` — pin content, return CID
  - `cat(cid)` — retrieve file by CID
  - `pin_ls(cid)` — check if CID is pinned
  - `is_available()` — check if IPFS daemon is running
  - CID format validation: CIDv0 (`Qm...`) and CIDv1 (`bafy...`)
  - Configurable max file size (default 10 MB), multipart/form-data encoding
  - Timeouts: 30s for add, 10s for read, 5s for availability check
- CLI `store` command updated:
  - `--ipfs` flag: pin file to IPFS and use real CID on-chain
  - `--ipfs-api URL` to configure IPFS endpoint
  - `--max-file-size` configurable size limit
  - Falls back to `local:<hash>` if IPFS unavailable (with warning)
- CLI `share` command updated:
  - `--ipfs` flag: pin file to IPFS before SHARE tx submission
  - Same `--ipfs-api` and `--max-file-size` options
- New CLI `retrieve` command:
  - `qbit retrieve <cid>` — fetch file from IPFS by CID
  - `--output FILE` — save to file instead of stdout
  - `--verify-hash` — check retrieved file hash against on-chain record
  - CID format validation before attempting retrieval
- All existing commands work unchanged without IPFS (backward-compatible)
- 35 new tests in `tests/test_ipfs.py` (client + CLI integration, mocked)

### Web Dashboard / Chain Explorer
- Single-page application at `/dashboard/` — self-contained HTML file (no build tools, no CDN)
  - **Live Stats Bar:** chain height, total transactions, pending pool size, validator count, avg block time (last 10 blocks)
  - **Recent Blocks:** table with index, hash, validator, tx count, timestamp; click to expand full detail + transactions; pagination via "Load More"
  - **Transaction Viewer:** search by TX ID; shows type, sender, nonce, payload (type-specific: document_hash for NOTARIZE, recipient+CID for SHARE, etc.)
  - **Validator Panel:** lists all registered validators with copy-on-click addresses
  - **Document Verifier:** input document hash, calls POST `/api/v1/verify`, shows verified/not-found result with proof details
  - **Pool Monitor:** pending count + visual breakdown bar by tx type (NOTARIZE/STORE/SHARE/REGISTER_KEY)
- WebSocket integration: subscribes to `new_block`, `new_tx`, `chain_stats`; toast notifications for new blocks/txs
- Auto-reconnect with exponential backoff (1s to 30s max)
- Configurable API base URL + auth token; settings persisted in `localStorage`
- Dark theme (#0d0d1a background, #00d4ff accent), responsive grid, monospace hashes
- XSS-safe: all dynamic content escaped via `createTextNode` before DOM insertion
- Static file route added to `RPCServer._mount_dashboard()` in `qbit_network/network/rpc.py`

## v0.3.0-sprint2 (2026-03-25)

### Storage: SQLite-Primary Chain Storage
- Removed dual-write architecture for disk-backed blockchains
  - `self.chain` list replaced with SQLite-only storage when `data_dir` is set
  - Blocks no longer held in memory; fetched from SQLite on demand via `get_block()`
  - Cached `_latest_block` (most accessed) and `_height` updated atomically on append/rollback
  - `_ChainProxy` provides backward-compatible list-like interface (`len()`, `bool()`, `[index]`, iteration)
  - In-memory mode (no `data_dir`) retains `_chain_list` for tests and ephemeral use
  - `SQLiteStore.get_blocks_range(start, end)` and `get_blocks_count()` added for range queries
  - Rollback refactored: blocks pre-fetched before SQLite deletion in `_rollback_to()`
  - `node.py` migrated from `self.blockchain.chain` to `get_block()` / `height` API
  - `get_next_nonce()` method added as explicit alias for `get_nonce()` (ISS-012)
  - All 331 existing tests pass (backward-compatible migration)

### Key Revocation (ISS-010)
- `REVOKE_KEY` transaction type for permanent on-chain key revocation
  - Payload: `key_type` (`signing`|`encryption`|`validator`) + `reason` (`compromised`|`rotation`|`decommission`)
  - Self-revocation only: tx sender must be the key owner
  - Idempotency: cannot revoke an already-revoked key
  - Genesis validator cannot be revoked (safety check)
- Revocation registry (`_revoked_keys: dict[str, dict]`) in Blockchain
  - `is_key_revoked(address, key_type)` and `get_revocation_info(address, key_type)` queries
- Processing in `_append_block`:
  - Signing revocation: address blocked from submitting further transactions (submit_tx + consensus.validate_block)
  - Encryption revocation: marked in registry for downstream consumers
  - Validator revocation: removed from `_validator_registry` and `consensus.validators`, cannot produce blocks
- Full rollback support in `_rollback_block`: revocations reverted, validators re-added from chain history
- SQLite `revoked_keys` table: `put_revocation()`, `get_revocation()`, `delete_revocation()`, `get_all_revocations()`
  - Atomic cleanup in `delete_blocks_from()` during reorg
  - Loaded on startup in `_load_from_sqlite()`
- Consensus integration: `_revoked_keys` injected into `ProofOfAuthority`; blocks with txs from revoked signers rejected
- RPC `qv_revokeKey(wallet_address, key_type, reason)` protected method in node.py
- 28 tests: payload validation (10), signing/encryption/validator revocation (6), idempotency (2), rollback (2), SQLite persistence (3), queries (2), adversarial (3)

### Infrastructure
- REST API gateway (`qbit_network/network/rest_api.py`) mounted at `/api/v1/` alongside existing JSON-RPC
  - 13 public GET endpoints: `/info`, `/health`, `/blocks` (paginated), `/blocks/latest`, `/blocks/:index`, `/blocks/hash/:hash`, `/txs/:txid`, `/txs/sender/:addr` (paginated), `/address/:addr`, `/notarizations/:hash`, `/validators`, `/pool`, `/pool/count`
  - 8 protected endpoints (bearer auth): `POST /txs`, `POST /wallets`, `GET /wallets`, `POST /notarize`, `POST /verify`, `POST /store`, `POST /share`, `POST /register-validator`
  - CORS middleware: configurable origins (default `*`), `GET/POST/OPTIONS` methods, `Authorization` + `Content-Type` headers, preflight `204` responses
  - Pagination: 1-based `page`, configurable `limit` (default 20, max 100)
  - Consistent response envelope: `{"data": ..., "error": null}` on success, `{"data": null, "error": {"code": N, "message": "..."}}` on error
  - Proper HTTP status codes: 200 OK, 201 Created, 204 No Content (preflight), 400 Bad Request, 401 Unauthorized, 404 Not Found, 429 Too Many Requests, 500 Internal Server Error
  - All handlers proxy to existing node RPC methods — no business logic duplication
  - Auth reuses `hmac.compare_digest` with the same RPC bearer token
  - Rate limiting inherited from RPC server middleware; `/health` and `/info` exempt

### WebSocket Subscriptions
- Real-time event subscriptions via WebSocket at `WS /ws` (`qbit_network/network/websocket.py`)
  - 3 channels: `new_block`, `new_tx`, `chain_stats`
  - JSON subscription protocol: `subscribe`, `unsubscribe`, `ping`/`pong` with structured error responses
  - `WebSocketManager` class: channel-based pub/sub with per-client tracking
  - Max 100 concurrent connections; max 10 subscriptions per client; 10 msg/s rate limit per client
  - Periodic `chain_stats` broadcast every 5s (height, tx_count, pool_size, peers) — skipped when no subscribers
  - aiohttp built-in heartbeat: 30s server ping, auto-close on timeout; 8 KB max message size
  - Events emitted on: block production, block receipt from P2P, tx submission via RPC and P2P
  - Graceful disconnect cleanup: all subscriptions removed, dead clients pruned during broadcast
  - WS route attached via `rpc.attach_websocket()` on the existing aiohttp app (no extra port)
  - No auth required (read-only public data); no private keys or auth tokens in event payloads

### Tests
- 47 new REST API tests (`tests/test_rest_api.py`): public endpoints, protected endpoints, CORS headers/preflight, response structure, input validation, auth enforcement
- 34 new WebSocket tests (`tests/test_websocket.py`): 18 unit tests (manager operations, rate limiting, broadcast, cleanup) + 16 integration tests (subscribe/unsubscribe, ping/pong, error handling, multi-channel, multi-client, disconnect cleanup, chain_stats)

## v0.3.0-sprint1 (2026-03-25)

### Protocol
- HELLO_AUTH mutual authentication: full server-side handler completing the 3-step ML-DSA-65 challenge-response flow (closes ISS-002)
  - Inbound `_handle_hello_auth_inbound`: validate fields, sign peer challenge, issue counter-challenge
  - `_handle_auth_response`: verify responder signature, send `auth_confirm`, mark peer authenticated
  - `_handle_auth_confirm`: verify initiator signature over counter-challenge, mark peer authenticated
  - Auth gating: `new_block`, `new_tx`, `get_blocks`, `blocks` rejected from unauthenticated v2 peers after grace period
  - Domain-separated signatures (`QBIT_AUTH_v2:` + challenge + signer address)
  - Single-use challenges (`os.urandom(32)`); monotonic deadline tracking (`time.monotonic()`)

### Validator Registry
- `REGISTER_VALIDATOR` transaction type for on-chain validator key distribution (closes ISS-008)
  - Payload: `validator_pubkey` (ML-DSA-65, 1952 bytes hex) + `validator_address` (derived, verified)
  - Reject duplicate registration: address already in registry returns error
  - Genesis validator auto-registered in memory on `init_chain()`
  - SQLite `validator_registry` table; full rollback support during reorg
  - RPC `qv_registerValidator` method for validator self-registration

### Security
- Token bucket rate limiting (closes Planned item):
  - P2P: per-peer IP, 20 msg/s sustained, 100 burst; disconnect after 3 violations; HELLO/HELLO_AUTH exempt
  - RPC: per-client IP, 10 req/s sustained, 50 burst; HTTP 429 with JSON-RPC error; GET / exempt
  - LRU cap at 10k tracked IPs; active-peer eviction exclusion; periodic stale cleanup every 60s
- 8 Round 13 audit findings fixed: auth grace period bypass (SPRINT1-001), v1 downgrade on failed auth (SPRINT1-002), auth handshake rate limiting (SPRINT1-004), validator registry cross-reference (SPRINT1-005), REGISTER_VALIDATOR overwrite (SPRINT1-006), rate limiter LRU eviction bypass (SPRINT1-008), monotonic auth deadline (SPRINT1-010), non-atomic validator SQLite during reorg (SPRINT1-012)
- 3 findings deferred to v0.4.0: responder-signs-before-verify (SPRINT1-003), genesis validator on-chain tx (SPRINT1-007), SQLite string concat in validator table (SPRINT1-011)

### Tests
- 75 new tests: 46 adversarial + 29 integration — 222 total (up from 147)
- CI pipeline split into 3 parallel jobs: unit, adversarial, integration; all jobs run Python 3.11 + 3.12 matrix (closes ISS-015)

## v0.2.1 (2026-03-25)

### CI Expansion (ISS-015)
- Adversarial test suite (`tests/test_adversarial.py`): 46 tests covering double-spend, replay, invalid signatures, future timestamps, nonce manipulation, oversized payloads, tampered block hashes, fork attacks, empty blocks, invalid validators, tx type mismatches, SSRF, and wallet tampering
- Integration test suite (`tests/test_integration.py`): 29 tests covering full lifecycle (wallet to proof), multi-wallet sharing, chain persistence/reload (SQLite + JSON migration), fork resolution convergence, concurrent submissions, proof export/verify, query APIs, and edge cases
- CI pipeline (`.github/workflows/ci.yml`) split into 3 parallel jobs: unit tests, adversarial tests, integration tests, plus a test summary job
- All jobs run across Python 3.11 and 3.12 matrix with JUnit XML reporting and artifact uploads
- Total test count: 222 tests (up from 147)

### Validator Registry (ISS-008)
- New `REGISTER_VALIDATOR` transaction type for on-chain validator key distribution
- Payload: `validator_pubkey` (ML-DSA-65, 1952 bytes hex) + `validator_address` (derived, verified)
- `_validator_registry` in Blockchain maps address to signing pubkey
- Genesis validator auto-registered during `init_chain()`
- Validators registered via on-chain tx are added to consensus for block production/validation
- SQLite `validator_registry` table for persistent storage across restarts
- Full rollback support: validator registrations reverted during reorg
- RPC `qv_registerValidator` method for validator self-registration
- RPC `qv_validators` and `qv_nodeInfo` updated to include on-chain registered validators
- Backward compatible: existing chains load without REGISTER_VALIDATOR txs

### Security
- Token bucket rate limiting for P2P (per-peer IP, 20 msg/s sustained, 100 burst) and RPC (per-client IP, 10 req/s sustained, 50 burst)
- P2P: peers disconnected after 3 rate-limit violations; HELLO/HELLO_AUTH exempt
- RPC: returns HTTP 429 with JSON-RPC error; GET / (health) exempt
- Localhost (127.0.0.1/::1) exempt from rate limiting in development
- LRU eviction at 10k tracked IPs; periodic stale-entry cleanup every 60s

### Protocol
- HELLO_AUTH full server-side handler: 3-step ML-DSA-65 challenge-response P2P authentication (closes ISS-002)
  - Outbound `connect()` sends `hello_auth` with 32-byte random challenge when signing keys are available
  - Inbound `_on_connect` handles `hello_auth` or `hello` (v1 fallback) as first message
  - `_handle_hello_auth_inbound`: validates fields, signs peer challenge, generates counter-challenge, sends `auth_response`
  - `_handle_auth_response`: verifies responder signature, signs counter-challenge, sends `auth_confirm`, marks `peer.authenticated = True`
  - `_handle_auth_confirm`: verifies initiator signature over counter-challenge, marks `peer.authenticated = True`
- Auth gating: `new_block`, `new_tx`, `get_blocks`, `blocks` messages rejected from unauthenticated v2 peers (after auth grace period)
- Peer fields: `challenge` (pending 32-byte nonce), `remote_pubkey` (authenticated ML-DSA pubkey), `auth_deadline` (10s timeout)
- Challenges are single-use (cleared before verification) and cryptographically random (`os.urandom`)
- Domain-separated signatures (`QBIT_AUTH_v2:` + challenge + signer address) prevent cross-protocol reuse
- Timestamp validated within MAX_BLOCK_DRIFT (30s); chain_id checked against CHAIN_ID
- v1 backwards compatibility: peers without signing keys use plain `hello`, skip auth requirement
- Protocol versioning: PROTOCOL_VERSION=2, negotiated via min(initiator, responder)
- Drop authority scoring — pure longest-chain fork resolution (first-seen wins on tie)

### Client
- HTML proof certificate export: `qbit proof <file> --format html`
- Auto key registration: `qbit wallet create --register --token TOKEN`
- CLI store command: `qbit store <file>` — record document hash on-chain
- CLI share command: `qbit share <file> --to <addr>` — ML-KEM encrypted sharing
- Full CLI: 7 commands (wallet, notarize, verify, proof, store, share, verify-proof)

### Infrastructure
- Dockerfile: multi-stage build (python:3.11-slim + liboqs 0.12.0)
- docker-compose.yml: 3-validator testnet with bridge network
- Exposed RPC ports 8545-8547 for external access

### Tests
- 166 tests passing across 7 test files
- 12 audit rounds, 116+ issues found and fixed

## v0.2.0 (2026-03-25)

### Protocol
- Fork resolution: longest valid chain rule replaces permanent divergence on conflicting blocks (closes ISS-003)
- Request-ID correlation: unsolicited MSG_BLOCKS are now rejected, preventing chain-split between honest nodes (closes ISS-005)

### Storage
- LevelDB/SQLite persistent backend replaces the in-memory chain; blocks and indices survive node restarts (closes ISS-006)

### Security
- TLS support for the RPC server via reverse-proxy mode; shared secrets no longer exposed over plain HTTP (closes ISS-004)

### Performance
- Consensus nonce validation reduced from O(n^2) to O(n) using a precomputed sender-count map (closes ISS-011)

### Client
- CLI tool added: wallet creation, key listing, and NOTARIZE submission from the command line
- Merkle proof export: `getProof` RPC method and CLI flag produce a portable JSON proof bundle

### Infrastructure
- CI/CD pipeline added: unit test suite runs on every push

### Known Issues Introduced
- ISS-015: CI pipeline covers unit tests only; adversarial and integration tests not yet included
- ISS-016: TLS termination is external (reverse proxy); in-process TLS deferred to v0.3.0
- ISS-017: CLI does not yet expose STORE or SHARE workflows

---

## v0.1.0 (2026-03-25)

### Initial Release

**Crypto Layer**
- ML-DSA-65 (CRYSTALS-Dilithium) signatures via liboqs
- ML-KEM-768 (CRYSTALS-Kyber) key encapsulation via liboqs
- SHA3-256 / SHAKE-256 hashing (stdlib hashlib)
- AES-256-GCM authenticated encryption (cryptography library)
- Domain-separated Merkle tree (prevents second-preimage attacks)

**Core**
- Dual-keypair wallet (ML-DSA signing + ML-KEM encryption)
- Wallet encryption: scrypt KDF (N=16384, r=8, p=1) + AES-256-GCM
- 4 transaction types: NOTARIZE, STORE, SHARE, REGISTER_KEY
- Block structure with ML-DSA signed headers and Merkle roots
- Proof of Authority consensus with round-robin validator selection
- Per-sender nonce ordering with cross-block replay prevention
- On-chain encryption key registry with version history
- First-notarization preservation (subsequent don't overwrite)
- Monotonic block timestamps: max(time(), parent+1)
- Self-produced blocks validated through consensus before commit

**Networking**
- TCP P2P with newline-delimited JSON protocol
- Peer discovery via gossip (MSG_PEERS)
- Chain sync with height exchange (MSG_STATUS)
- SSRF protection (private IPs, metadata endpoints, blocked ports)
- MAX_PEERS=50 enforced on inbound + outbound
- 10-second HELLO timeout on inbound connections

**RPC**
- JSON-RPC 2.0 with batch support (max 50)
- Bearer token authentication (constant-time comparison)
- 22 methods (11 public, 11 protected)
- Body size limit (1MB, enforced at aiohttp level)
- All parameters validated with isinstance checks
- Error messages sanitized (200 char max)

**Persistence**
- Atomic chain writes (tempfile + os.replace)
- Atomic wallet writes with 0o600 permissions
- Chain load validation (hash chain, tx sigs, block sigs)
- Wallet persistence across node restarts

**Security**
- 9 rounds of security audit (104 issues found and resolved)
- See tracker/AUDIT_LOG.md for complete audit trail
