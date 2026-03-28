# QBit Network Roadmap: v0.8.0 -> v1.0.0

**Ngay tao:** 2026-03-28 | **Tac gia:** Tech Lead + Product Owner
**Trang thai hien tai:** v0.8.0 "Enterprise Foundation" — 14 TX types, 1781 tests, 23 audit rounds

---

## Tong quan

| Version | Ten ma | Sprints | TX types moi | Tests du kien | Thoi gian |
|---------|--------|---------|--------------|---------------|-----------|
| v0.9.0 | Identity & Governance | 5 (10 tuan) | 8 | ~2350 | Q2 2026 |
| v0.10.0 | Network Hardening | 4 (8 tuan) | 0 | ~2650 | Q3 2026 |
| v1.0.0 | Production Ready | 4 (8 tuan) | 0 | ~2900 | Q4 2026 |

Tong cong: 13 sprints, 26 tuan (~6 thang) tu v0.8.0 den v1.0.0.

---

# v0.9.0 "Identity & Governance"

## Muc tieu

Them W3C DID method (`did:qbit`), Verifiable Credentials, token governance (burn/freeze/approve), multi-sig transactions, TypeScript SDK, va token explorer trong NextJS dashboard. Nang tong so TX types tu 14 len 22.

## TX Types moi (8 loai)

### DID Operations (3 TX types)

| TX Type | Muc dich | Fee (QBIT) | Weight |
|---------|----------|-----------|--------|
| CREATE_DID | Tao DID document moi | 0.1 | 10,000,000 |
| UPDATE_DID | Cap nhat DID document | 0.01 | 1,000,000 |
| DEACTIVATE_DID | Vo hieu hoa DID | 0.001 | 100,000 |

**CREATE_DID Payload:**
```json
{
  "id": "did:qbit:<address>",
  "authentication": ["<ml-dsa-65-pubkey-hex>"],
  "keyAgreement": ["<ml-kem-768-pubkey-hex>"],
  "service": [
    {"id": "#notarize", "type": "QBitNotarization", "serviceEndpoint": "https://..."}
  ],
  "controller": "<address>"
}
```
- `id`: bat buoc, phai khop voi `did:qbit:<sender_address>`
- `authentication`: bat buoc, it nhat 1 ML-DSA-65 public key (hex)
- `keyAgreement`: tuy chon, ML-KEM-768 public keys (hex)
- `service`: tuy chon, mang service endpoints (max 10, moi endpoint max 2048 chars)
- `controller`: mac dinh = sender address

**UPDATE_DID Payload:**
```json
{
  "did": "did:qbit:<address>",
  "patch": {
    "add_authentication": ["<new-pubkey-hex>"],
    "remove_authentication": ["<old-pubkey-hex>"],
    "add_service": [{"id": "#new", "type": "...", "serviceEndpoint": "..."}],
    "remove_service": ["#old"],
    "set_controller": "<new-address>"
  }
}
```
- `did`: bat buoc, phai thuoc ve sender hoac controller hien tai
- `patch`: bat buoc, it nhat 1 operation
- Khong cho phep xoa het authentication keys (phai con it nhat 1)

**DEACTIVATE_DID Payload:**
```json
{
  "did": "did:qbit:<address>"
}
```
- Chi controller hoac owner duoc deactivate
- Sau khi deactivate: DID khong the update, khong the resolve

### Token Governance (3 TX types)

| TX Type | Muc dich | Fee (QBIT) | Weight |
|---------|----------|-----------|--------|
| BURN_TOKEN | Dot token vinh vien | 0.001 | 100,000 |
| FREEZE_TOKEN | Dong bang/giai phong tai khoan token | 0.01 | 1,000,000 |
| APPROVE_TOKEN | Cho phep dia chi khac chi tieu token | 0.001 | 100,000 |

**BURN_TOKEN Payload:**
```json
{
  "token_id": "<32-hex-chars>",
  "amount": 1000
}
```
- `token_id`: bat buoc, 32 hex chars, token phai ton tai
- `amount`: bat buoc, > 0, sender phai co du so du
- Giam `total_minted` cua token registry
- Bat ky ai co token deu co the burn (khong chi issuer)

**FREEZE_TOKEN Payload:**
```json
{
  "token_id": "<32-hex-chars>",
  "target": "<address>",
  "freeze": true
}
```
- `token_id`: bat buoc, chi issuer moi duoc freeze/unfreeze
- `target`: dia chi bi dong bang, khac sender
- `freeze`: `true` = dong bang, `false` = giai phong
- Tai khoan bi freeze khong the transfer hoac bi transfer den

**APPROVE_TOKEN Payload:**
```json
{
  "token_id": "<32-hex-chars>",
  "spender": "<address>",
  "amount": 5000
}
```
- `token_id`: bat buoc, token phai ton tai
- `spender`: bat buoc, dia chi duoc uy quyen, khac sender
- `amount`: bat buoc, >= 0 (0 = huy uy quyen)
- Ghi vao `_token_allowances[(token_id, owner, spender)] = amount`

### Multi-Sig (1 TX type)

| TX Type | Muc dich | Fee (QBIT) | Weight |
|---------|----------|-----------|--------|
| MULTISIG | Giao dich M-of-N | base_fee * 1.5 | base_weight * 1.5 |

**MULTISIG Payload:**
```json
{
  "threshold": 2,
  "signers": ["<addr1>", "<addr2>", "<addr3>"],
  "inner_tx": {
    "type": "TRANSFER",
    "sender": "<multisig_address>",
    "recipient": "<addr>",
    "payload": {"amount": 100000000}
  },
  "signatures": [
    {"signer": "<addr1>", "signature": "<hex>"},
    {"signer": "<addr2>", "signature": "<hex>"}
  ]
}
```
- `threshold`: M trong M-of-N, 1 <= M <= N <= 10
- `signers`: danh sach N dia chi, moi dia chi phai co REGISTER_KEY
- `inner_tx`: giao dich ben trong (TRANSFER, TRANSFER_TOKEN, BURN_TOKEN, ...)
- `signatures`: it nhat M chu ky hop le tu signers
- Multisig address: `SHA3-256(sorted(signers) + threshold)` voi prefix `qm1`
- Inner TX types ho tro: TRANSFER, TRANSFER_TOKEN, BURN_TOKEN, APPROVE_TOKEN, NOTARIZE, STORE
- Khong ho tro nested MULTISIG

### Verifiable Credentials (1 TX type)

| TX Type | Muc dich | Fee (QBIT) | Weight |
|---------|----------|-----------|--------|
| ISSUE_VC | Phat hanh Verifiable Credential | 0.02 | 2,000,000 |

**ISSUE_VC Payload:**
```json
{
  "credential_id": "<sha3-256-hex>",
  "issuer_did": "did:qbit:<issuer_address>",
  "subject_did": "did:qbit:<subject_address>",
  "credential_type": ["VerifiableCredential", "DocumentAttestation"],
  "issuance_date": "2026-04-15T10:00:00Z",
  "expiration_date": "2027-04-15T10:00:00Z",
  "credential_hash": "<sha3-256-of-full-credential-json>",
  "revocable": true
}
```
- `credential_id`: bat buoc, SHA3-256 hex, unique tren chain
- `issuer_did`: bat buoc, phai la DID da active cua sender
- `subject_did`: bat buoc, phai la DID da ton tai
- `credential_type`: bat buoc, mang strings, phai chua "VerifiableCredential"
- `issuance_date`: bat buoc, ISO 8601
- `expiration_date`: tuy chon, phai sau issuance_date
- `credential_hash`: bat buoc, hash cua full credential document (luu ngoai chain)
- `revocable`: mac dinh true; neu false thi khong the revoke sau nay
- VC nay la on-chain anchor; full credential document luu off-chain (IPFS, server, ...)
- Issuer co the revoke bang UPDATE_DID them vao revocation list

## API Endpoints moi

### REST

| Method | Path | Auth | Muc dich |
|--------|------|------|----------|
| GET | /api/v1/did/{did} | Public | Resolve DID document |
| GET | /api/v1/did/{did}/history | Public | DID document version history |
| POST | /api/v1/create-did | Protected | Tao DID |
| POST | /api/v1/update-did | Protected | Cap nhat DID |
| POST | /api/v1/deactivate-did | Protected | Vo hieu hoa DID |
| POST | /api/v1/burn-token | Protected | Dot token |
| POST | /api/v1/freeze-token | Protected | Dong bang tai khoan token |
| POST | /api/v1/approve-token | Protected | Uy quyen chi tieu |
| GET | /api/v1/token/{id}/allowance/{owner}/{spender} | Public | Xem allowance |
| GET | /api/v1/token/{id}/frozen/{address} | Public | Kiem tra frozen status |
| POST | /api/v1/multisig/create | Protected | Tao multisig address |
| POST | /api/v1/multisig/submit | Protected | Gui multisig TX |
| GET | /api/v1/multisig/{address} | Public | Thong tin multisig |
| POST | /api/v1/issue-vc | Protected | Phat hanh VC |
| GET | /api/v1/vc/{credential_id} | Public | Xem VC anchor |
| GET | /api/v1/vc/issuer/{did} | Public | Danh sach VC cua issuer |

### JSON-RPC (16 methods moi)

| Method | Auth | Muc dich |
|--------|------|----------|
| qv_resolveDID | Public | Resolve DID document |
| qv_getDIDHistory | Public | DID version history |
| qv_createDID | Protected | Tao DID |
| qv_updateDID | Protected | Cap nhat DID |
| qv_deactivateDID | Protected | Vo hieu hoa DID |
| qv_burnToken | Protected | Dot token |
| qv_freezeToken | Protected | Dong bang tai khoan |
| qv_approveToken | Protected | Uy quyen chi tieu |
| qv_getTokenAllowance | Public | Xem allowance |
| qv_getTokenFrozenStatus | Public | Kiem tra frozen |
| qv_createMultisig | Protected | Tao multisig address |
| qv_submitMultisig | Protected | Gui multisig TX |
| qv_getMultisigInfo | Public | Thong tin multisig |
| qv_issueVC | Protected | Phat hanh VC |
| qv_getVC | Public | Xem VC anchor |
| qv_getVCsByIssuer | Public | Danh sach VC cua issuer |

### WebSocket Channels moi

| Channel | Events |
|---------|--------|
| did | DIDCreated, DIDUpdated, DIDDeactivated |
| vc | VCIssued |
| governance | TokenBurned, TokenFrozen, TokenUnfrozen, TokenApproved |
| multisig | MultisigCreated, MultisigExecuted |

## Sprint Plan

### Sprint 1: DID Core (Tuan 1-2)

**Agents:** `blockchain-dev` (chinh), `protocol-designer` (review)

**Deliverables:**
- [ ] TxType enum: them CREATE_DID, UPDATE_DID, DEACTIVATE_DID
- [ ] config.py: fees, weights, DID constants (MAX_DID_SERVICES=10, MAX_SERVICE_ENDPOINT_LEN=2048)
- [ ] transaction.py: _ALLOWED_KEYS, validate_payload() cho 3 DID types
- [ ] transaction.py: factory methods (create_did, update_did, deactivate_did)
- [ ] blockchain.py: `_did_registry` dict: `did_string -> {document, version, active, created_block, updated_block}`
- [ ] blockchain.py: `_append_block_inner` branches cho 3 DID types
- [ ] state_ops.py: DID entries trong state trie (`did:<address> -> document_hash`)
- [ ] receipt_ops.py: DIDCreated, DIDUpdated, DIDDeactivated events
- [ ] rollback.py: rollback DID operations
- [ ] store.py: `dids` SQLite table (did TEXT PK, document JSON, version INT, active BOOL, created_block INT, updated_block INT)
- [ ] persistence.py: load/save DID state
- [ ] tx_pool.py: pool admission validation cho DID TX types
- [ ] query.py: resolve_did, get_did_history
- [ ] Unit tests: ~60 tests

**Files thay doi:**
```
qbit_network/config.py
qbit_network/core/transaction.py
qbit_network/core/blockchain.py
qbit_network/core/state_ops.py
qbit_network/core/receipt_ops.py
qbit_network/core/rollback.py
qbit_network/core/store.py
qbit_network/core/persistence.py
qbit_network/core/tx_pool.py
qbit_network/core/query.py
tests/test_did.py (moi)
```

**Tieu chi chap nhan:**
- DID resolve tra ve W3C DID Core spec compliant document
- DID patch operations la idempotent
- Khong the xoa het authentication keys
- Rollback khoi phuc DID state chinh xac

---

### Sprint 2: Token Governance (Tuan 3-4)

**Agents:** `blockchain-dev` (chinh), `security-auditor` (review allowance logic)

**Deliverables:**
- [ ] TxType enum: them BURN_TOKEN, FREEZE_TOKEN, APPROVE_TOKEN
- [ ] config.py: fees, weights cho 3 types; MAX_ALLOWANCE_PER_TOKEN=1000
- [ ] transaction.py: _ALLOWED_KEYS, validate_payload(), factory methods
- [ ] blockchain.py: `_token_frozen` set: `(token_id, address)`, `_token_allowances` dict: `(token_id, owner, spender) -> amount`
- [ ] blockchain.py: `_append_block_inner` branches cho BURN, FREEZE, APPROVE
- [ ] blockchain.py: sua TRANSFER_TOKEN de check frozen status va allowance (transferFrom logic)
- [ ] TRANSFER_TOKEN payload mo rong: them field `from` (tuy chon) cho delegated transfer
  - Khi co `from`: spender dung allowance de transfer tu `from` -> `recipient`
  - Khi khong co `from`: hoat dong nhu cu (sender -> recipient)
- [ ] state_ops.py: frozen va allowance trong state trie
- [ ] receipt_ops.py: TokenBurned, TokenFrozen, TokenUnfrozen, TokenApproved events
- [ ] rollback.py: rollback cho burn (khoi phuc total_minted + balance), freeze, approve
- [ ] store.py: `token_frozen` table, `token_allowances` table
- [ ] persistence.py: load/save frozen va allowance state
- [ ] tx_pool.py: pool admission cho 3 types moi
- [ ] query.py: get_token_allowance, is_token_frozen
- [ ] REST + JSON-RPC endpoints cho governance
- [ ] Unit tests: ~80 tests

**Files thay doi:**
```
qbit_network/config.py
qbit_network/core/transaction.py
qbit_network/core/blockchain.py
qbit_network/core/state_ops.py
qbit_network/core/receipt_ops.py
qbit_network/core/rollback.py
qbit_network/core/store.py
qbit_network/core/persistence.py
qbit_network/core/tx_pool.py
qbit_network/core/query.py
qbit_network/network/rpc.py
qbit_network/network/rest_api.py
tests/test_token_governance.py (moi)
```

**Rui ro:**
- MEDIUM: Allowance + transferFrom la attack surface phuc tap (double-spend, front-running). Can security audit ky luong.
- Giai phap: Approve chi set (khong increment), transferFrom giam allowance truoc khi transfer.

---

### Sprint 3: Multi-Sig Transactions (Tuan 5-6)

**Agents:** `blockchain-dev` (chinh), `protocol-designer` (multisig address scheme), `security-auditor` (sig aggregation review)

**Deliverables:**
- [ ] TxType enum: them MULTISIG
- [ ] config.py: MAX_MULTISIG_SIGNERS=10, MULTISIG_ADDRESS_PREFIX="qm1"
- [ ] transaction.py: MULTISIG payload validation
  - Verify threshold <= len(signers) va threshold >= 1
  - Verify inner_tx type la allowed (TRANSFER, TRANSFER_TOKEN, BURN_TOKEN, APPROVE_TOKEN, NOTARIZE, STORE)
  - Verify khong nested MULTISIG
- [ ] blockchain.py: `_multisig_registry` dict: `multisig_address -> {threshold, signers, created_block}`
- [ ] blockchain.py: multisig address derivation: `qm1` + SHA3-256(sorted(signers) + str(threshold))[:40].hex()
- [ ] blockchain.py: `_append_block_inner` MULTISIG branch:
  1. Verify multisig address da dang ky (hoac tu dang ky trong cung TX)
  2. Verify >= threshold signatures hop le (ML-DSA-65 verify tung cai)
  3. Execute inner_tx nhu normal (sender = multisig_address)
- [ ] wallet.py: `create_multisig_address(threshold, signers)` utility
- [ ] state_ops.py: multisig entries trong state trie
- [ ] receipt_ops.py: MultisigCreated, MultisigExecuted events
- [ ] rollback.py: rollback multisig operations
- [ ] store.py: `multisig` SQLite table
- [ ] persistence.py: load/save multisig state
- [ ] tx_pool.py: pool admission voi signature validation
- [ ] REST + JSON-RPC endpoints
- [ ] Unit tests: ~70 tests

**Files thay doi:**
```
qbit_network/config.py
qbit_network/core/transaction.py
qbit_network/core/blockchain.py
qbit_network/core/wallet.py
qbit_network/core/state_ops.py
qbit_network/core/receipt_ops.py
qbit_network/core/rollback.py
qbit_network/core/store.py
qbit_network/core/persistence.py
qbit_network/core/tx_pool.py
qbit_network/network/rpc.py
qbit_network/network/rest_api.py
tests/test_multisig.py (moi)
```

**Rui ro:**
- HIGH: Replay attack tren multisig signatures. Moi signature phai bao gom nonce cua multisig address + chain_id + inner_tx_hash.
- MEDIUM: Signature verification cho N signers co the cham (N * 30ms worst case = 300ms cho 10 signers). Chap nhan duoc cho v0.9.0.

**Dependency:** Can REGISTER_KEY da ton tai cho tat ca signers truoc khi tao multisig.

---

### Sprint 4: Verifiable Credentials + DID API (Tuan 7-8)

**Agents:** `blockchain-dev` (chinh), `docs-writer` (DID method spec)

**Deliverables:**
- [ ] TxType enum: them ISSUE_VC
- [ ] config.py: fees, weights, MAX_VC_TYPES=10, MAX_CREDENTIAL_TYPE_LEN=128
- [ ] transaction.py: ISSUE_VC payload validation
- [ ] blockchain.py: `_vc_registry` dict: `credential_id -> {issuer_did, subject_did, types, issuance_date, expiration_date, credential_hash, revocable, revoked, block_index}`
- [ ] blockchain.py: `_append_block_inner` ISSUE_VC branch
- [ ] VC revocation thong qua UPDATE_DID (them credential_id vao revocation_list trong DID document)
- [ ] state_ops.py: VC entries trong state trie
- [ ] receipt_ops.py: VCIssued event
- [ ] rollback.py: rollback VC operations
- [ ] store.py: `verifiable_credentials` SQLite table
- [ ] persistence.py: load/save VC state
- [ ] tx_pool.py: pool admission (verify DID active, verify credential_id unique)
- [ ] query.py: get_vc, get_vcs_by_issuer, get_vcs_by_subject, verify_vc
- [ ] REST endpoints cho DID + VC (tat ca 16 endpoints listed above)
- [ ] JSON-RPC methods cho DID + VC (tat ca 16 methods listed above)
- [ ] WebSocket channels: did, vc, governance, multisig
- [ ] docs/DID_METHOD_SPEC.md: W3C DID method specification cho `did:qbit`
- [ ] Unit tests: ~60 tests

**Files thay doi:**
```
qbit_network/config.py
qbit_network/core/transaction.py
qbit_network/core/blockchain.py
qbit_network/core/state_ops.py
qbit_network/core/receipt_ops.py
qbit_network/core/rollback.py
qbit_network/core/store.py
qbit_network/core/persistence.py
qbit_network/core/tx_pool.py
qbit_network/core/query.py
qbit_network/network/rpc.py
qbit_network/network/rest_api.py
qbit_network/network/websocket.py
docs/DID_METHOD_SPEC.md (moi)
tests/test_vc.py (moi)
```

**Dependency:** Sprint 1 (DID core) phai hoan thanh truoc.

---

### Sprint 5: TypeScript SDK + Token Explorer + Audit + Release (Tuan 9-10)

**Agents:** `frontend-dev` (SDK + dashboard), `security-auditor` (audit round 24-25), `docs-writer` (changelog), `test-runner` (full suite)

**Deliverables:**

**TypeScript SDK (`sdk/qbit-sdk-ts/`):**
- [ ] Package: `@qbit-network/sdk` (npm)
- [ ] `QBitClient` class: async HTTP client dung `fetch` API
- [ ] Public methods: getInfo, getBlock, getBalance, getSupply, getFeeInfo, getValidators, getStateProof, verifyDocument, getReceipt, getFinalizedHeight, getEvents, resolveDID, getVC
- [ ] Protected methods: createWallet, listWallets, transfer, notarize, store, share, stake, delegate, unstake, registerValidator, createDID, updateDID, deactivateDID, issueToken, mintToken, transferToken, burnToken, freezeToken, approveToken, issueVC
- [ ] Multisig helpers: createMultisigAddress, submitMultisig
- [ ] WebSocket client: subscribe/unsubscribe voi typed events
- [ ] Type definitions: full TypeScript types cho tat ca models
- [ ] Zero runtime dependencies (chi `fetch` + `WebSocket` globals)
- [ ] `package.json`, `tsconfig.json`, `README.md`
- [ ] ESM + CJS dual build
- [ ] Unit tests: ~30 tests (vitest)

**Token Explorer (NextJS dashboard):**
- [ ] Page: `/tokens` — danh sach tat ca tokens voi pagination
- [ ] Page: `/tokens/[id]` — chi tiet token (holders, supply, transfers, frozen accounts)
- [ ] Page: `/did/[did]` — DID document viewer
- [ ] Component: `<TokenTransferHistory>` — lich su transfer cua token
- [ ] Component: `<DIDDocument>` — render W3C DID document
- [ ] Tich hop voi existing dashboard layout

**Security Audit:**
- [ ] Round 24: Full audit cua 8 TX types moi (DID, governance, multisig, VC)
- [ ] Focus areas: allowance double-spend, multisig replay, DID controller escalation, VC forgery
- [ ] Round 25: Verification audit cua R24 fixes
- [ ] Muc tieu: 0 HIGH issues open

**Release:**
- [ ] config.py: VERSION = "0.9.0", PROTOCOL_VERSION = 5
- [ ] PROTOCOL.md v5 update
- [ ] CHANGELOG.md entries
- [ ] FEATURES.md update
- [ ] openapi.yaml update (them 16 endpoints)
- [ ] README.md update

**Tests:**
- [ ] Integration tests: DID + VC end-to-end flow
- [ ] Integration tests: multisig + token governance flow
- [ ] Adversarial tests: allowance abuse, DID takeover, multisig replay
- [ ] Target: ~2350 tests total (~570 moi)

**Files thay doi:**
```
sdk/qbit-sdk-ts/ (thu muc moi)
dashboard/ (thu muc moi hoac mo rong)
qbit_network/config.py
docs/openapi.yaml
docs/PROTOCOL.md
tracker/CHANGELOG.md
tracker/FEATURES.md
tracker/AUDIT_LOG.md
README.md
CLAUDE.md
tests/test_did_integration.py (moi)
tests/test_governance_adversarial.py (moi)
```

## v0.9.0 Tong ket

| Metric | Truoc | Sau |
|--------|-------|-----|
| TX Types | 14 | 22 |
| Tests | 1781 | ~2350 |
| Audit Rounds | 23 | 25 |
| Protocol Version | 4 | 5 |
| REST Endpoints | ~42 | ~58 |
| JSON-RPC Methods | ~35 | ~51 |
| Source Lines | ~11,350 | ~14,500 |

## v0.9.0 Dependency Graph

```
Sprint 1 (DID Core)
    |
    v
Sprint 4 (VC + DID API)  <-- phu thuoc Sprint 1
    |
Sprint 2 (Token Governance)  <-- doc lap
    |
Sprint 3 (Multi-Sig)  <-- doc lap, nhung APPROVE_TOKEN ho tro tot hon neu co Sprint 2
    |
    v
Sprint 5 (SDK + Explorer + Audit)  <-- phu thuoc tat ca sprints truoc
```

Sprint 2 va Sprint 3 co the chay song song neu co 2 developers.

---

# v0.10.0 "Network Hardening"

## Muc tieu

Chuyen tu single-node dev sang multi-node testnet thuc te. Them peer discovery (DNS seeds + DHT), chain sync protocol, Prometheus metrics, PostgreSQL sync bridge, va CI/CD pipeline day du. KHONG them TX types moi — tap trung vao infrastructure.

## TX Types moi

Khong co. v0.10.0 la pure infrastructure release.

## API Endpoints moi

### REST

| Method | Path | Auth | Muc dich |
|--------|------|------|----------|
| GET | /api/v1/metrics | Public | Prometheus metrics (text format) |
| GET | /api/v1/peers | Public | Danh sach peers dang ket noi |
| GET | /api/v1/peers/{id} | Public | Chi tiet peer |
| GET | /api/v1/sync/status | Public | Trang thai sync |
| POST | /api/v1/admin/add-peer | Protected | Them peer thu cong |
| POST | /api/v1/admin/remove-peer | Protected | Xoa peer |
| GET | /api/v1/health | Public | Health check (cho load balancer) |
| GET | /api/v1/health/ready | Public | Readiness check (synced + peers > 0) |

### JSON-RPC (6 methods moi)

| Method | Auth | Muc dich |
|--------|------|----------|
| qv_getPeers | Public | Danh sach peers |
| qv_getSyncStatus | Public | Trang thai sync |
| qv_addPeer | Protected | Them peer thu cong |
| qv_removePeer | Protected | Xoa peer |
| qv_getMetrics | Public | Metrics JSON format |
| qv_getNodeHealth | Public | Health + readiness |

## Sprint Plan

### Sprint 1: Multi-Node Testnet + Chain Sync (Tuan 1-2)

**Agents:** `devops` (chinh), `blockchain-dev` (sync protocol), `protocol-designer` (review)

**Deliverables:**

**Chain Sync Protocol:**
- [ ] P2P message: `get_blocks(start_index, count, max=500)` — request block range
- [ ] P2P message: `blocks(blocks[])` — response voi block data
- [ ] P2P message: `get_chain_info()` — request chain height + head hash
- [ ] P2P message: `chain_info(height, head_hash, finalized_height)` — response
- [ ] `SyncManager` class (`qbit_network/network/sync.py`):
  - State machine: IDLE -> SYNCING -> SYNCED
  - Chon peer co chain cao nhat lam sync source
  - Download blocks theo batch (100 blocks/request)
  - Validate tung block qua `validate_block()` + `append_block()`
  - Checkpoint: luu progress moi 100 blocks
  - Retry: chuyen sang peer khac neu timeout (30s) hoac nhan invalid blocks
  - Fast sync mode: download headers truoc, verify PoA chain, sau do download full blocks
- [ ] node.py: integrate SyncManager vao startup flow
- [ ] P2P: sua `_on_new_block` de khong broadcast khi dang sync

**Multi-Node Testnet:**
- [ ] `testnet/` directory voi docker-compose.yml cho 3 validators + 1 full node
- [ ] `testnet/genesis.json`: pre-configured genesis voi 3 validator addresses
- [ ] `testnet/keys/`: pre-generated validator wallet files (test only, password "test")
- [ ] `testnet/README.md`: huong dan chay testnet
- [ ] Dockerfile: multi-stage build, Python 3.11-slim base, liboqs installed
- [ ] docker-compose.yml: 4 services (val1, val2, val3, fullnode) voi networking

**Files thay doi:**
```
qbit_network/network/sync.py (moi)
qbit_network/network/p2p.py
qbit_network/node.py
testnet/ (thu muc moi)
testnet/docker-compose.yml
testnet/genesis.json
testnet/keys/
testnet/README.md
Dockerfile (moi)
tests/test_sync.py (moi)
```

**Tests:** ~60 tests (sync state machine, block download, checkpoint, retry)

**Rui ro:**
- HIGH: Chain sync la diem yeu nhat cua blockchain — invalid blocks, slow peers, eclipse attacks. Can ky test voi adversarial peers.
- Giai phap: Download tu nhieu peers dong thoi, cross-verify headers.

---

### Sprint 2: Peer Discovery + DNS Seeds (Tuan 3-4)

**Agents:** `devops` (chinh), `blockchain-dev` (DHT), `security-auditor` (Sybil review)

**Deliverables:**

**DNS Seed Discovery:**
- [ ] config.py: `DNS_SEEDS = ["seeds.qbit.network"]` (configurable)
- [ ] `DiscoveryManager` class (`qbit_network/network/discovery.py`):
  - DNS TXT record query cho seed nodes: `TXT "qbit:peer:<ip>:<port>"`
  - Bootstrap: query DNS seeds khi peers < MIN_PEERS (3)
  - Peer exchange: P2P message `get_peers()` -> `peers(addr_list)` (max 20 peers)
  - Peer scoring: track latency, uptime, ban count
  - Eviction: remove peer voi score thap nhat khi peers == MAX_PEERS
  - Blacklist: ban peer sau 3 violations (1 gio default)
- [ ] Kademlia-style DHT (don gian hoa):
  - Node ID = SHA3-256(pubkey)[:20]
  - K-bucket: 20 buckets x 8 nodes
  - FIND_NODE message: tim peers gan nhat voi target ID
  - Khong luu data trong DHT — chi dung cho peer discovery
  - Anti-Sybil: moi node phai co valid REGISTER_KEY on-chain de tham gia DHT
- [ ] P2P: them message types: get_peers, peers, find_node, found_nodes
- [ ] node.py: integrate DiscoveryManager

**Files thay doi:**
```
qbit_network/config.py
qbit_network/network/discovery.py (moi)
qbit_network/network/p2p.py
qbit_network/node.py
tests/test_discovery.py (moi)
```

**Tests:** ~50 tests (DNS mock, peer exchange, DHT routing, Sybil resistance)

**Rui ro:**
- HIGH: Sybil attack qua peer discovery. Giai phap: on-chain identity requirement cho DHT participation.
- MEDIUM: DNS poisoning. Giai phap: hardcode backup seed IPs, verify peer identity qua P2P handshake.

---

### Sprint 3: Prometheus Metrics + PostgreSQL Bridge (Tuan 5-6)

**Agents:** `devops` (chinh), `perf-engineer` (metrics selection)

**Deliverables:**

**Prometheus Metrics (`qbit_network/network/metrics.py`):**
- [ ] Counter: `qbit_blocks_total` — tong so blocks
- [ ] Counter: `qbit_transactions_total{type}` — TX theo type
- [ ] Counter: `qbit_p2p_messages_total{type,direction}` — P2P messages
- [ ] Counter: `qbit_rpc_requests_total{method,status}` — RPC requests
- [ ] Gauge: `qbit_chain_height` — chieu cao chain
- [ ] Gauge: `qbit_finalized_height` — chieu cao finalized
- [ ] Gauge: `qbit_mempool_size` — so TX trong pool
- [ ] Gauge: `qbit_peers_connected` — so peers
- [ ] Gauge: `qbit_sync_status` — 0=syncing, 1=synced
- [ ] Gauge: `qbit_total_stake` — tong stake
- [ ] Histogram: `qbit_block_processing_seconds` — thoi gian xu ly block
- [ ] Histogram: `qbit_tx_validation_seconds` — thoi gian validate TX
- [ ] Histogram: `qbit_p2p_message_size_bytes` — kich thuoc P2P message
- [ ] REST: GET /metrics (Prometheus text format)
- [ ] REST: GET /api/v1/metrics (JSON format)
- [ ] Grafana dashboard JSON: `testnet/grafana/qbit-dashboard.json`
  - Panels: chain height, TPS, mempool, peers, block time, fees, stake distribution
  - Alerts: chain stall (no blocks > 30s), peer count < 2, mempool > 5000

**PostgreSQL Sync Bridge (`qbit_network/bridge/postgres.py`):**
- [ ] `PostgresBridge` class:
  - Ket noi PostgreSQL qua `asyncpg` (optional dependency)
  - Schema: blocks, transactions, receipts, events, balances, tokens, dids tables
  - Full index: block_index, tx_id, sender, recipient, event_type, token_id, did
  - Sync mode: follow chain tip, insert blocks khi append, delete khi rollback
  - Backfill mode: sync tu block 0 khi khoi dong lan dau
  - Config: POSTGRES_URL env var, POSTGRES_SYNC_ENABLED=0|1
- [ ] SQL migration files: `bridge/migrations/001_init.sql`
- [ ] Khong phu thuoc vao PostgreSQL de chay node — chi la optional sync target

**Files thay doi:**
```
qbit_network/network/metrics.py (moi)
qbit_network/network/rest_api.py
qbit_network/bridge/ (thu muc moi)
qbit_network/bridge/__init__.py
qbit_network/bridge/postgres.py
bridge/migrations/001_init.sql (moi)
testnet/grafana/qbit-dashboard.json (moi)
testnet/docker-compose.yml (them prometheus + grafana + postgres)
requirements-bridge.txt (moi: asyncpg)
tests/test_metrics.py (moi)
tests/test_postgres_bridge.py (moi)
```

**Tests:** ~50 tests (metrics counters, Prometheus format, PostgreSQL mock)

**Rui ro:**
- LOW: asyncpg dependency. Giai phap: optional import, node van chay binh thuong khong co PostgreSQL.
- MEDIUM: PostgreSQL sync lag under high load. Giai phap: batch inserts (100 blocks/commit), async write queue.

---

### Sprint 4: CI/CD + Docker + Audit + Release (Tuan 7-8)

**Agents:** `devops` (CI/CD), `security-auditor` (audit round 26-27), `test-runner` (full matrix), `docs-writer`

**Deliverables:**

**GitHub Actions CI/CD (`.github/workflows/`):**
- [ ] `ci.yml`: matrix test (Python 3.11, 3.12, 3.13) x (ubuntu-latest, macos-latest)
  - Step 1: Install liboqs dependencies
  - Step 2: pip install -e .[dev]
  - Step 3: pytest --tb=short -q (fail fast)
  - Step 4: Coverage report (target >= 85%)
  - Trigger: push to main, pull requests
- [ ] `docker.yml`: build + push Docker image to ghcr.io
  - Multi-arch: linux/amd64, linux/arm64
  - Tag: version tag + latest
  - Trigger: tag push (v*)
- [ ] `release.yml`: automated release notes tu CHANGELOG.md
- [ ] `security.yml`: weekly dependency scan (pip-audit)

**Docker:**
- [ ] `Dockerfile`: multi-stage build
  - Stage 1: build liboqs from source (C compiler + cmake)
  - Stage 2: Python 3.11-slim + liboqs.so + app code
  - Final image: ~200MB target
- [ ] `.dockerignore`: exclude tests, docs, .git, __pycache__
- [ ] `docker-compose.yml` (root): single-node dev setup
- [ ] Health check: `HEALTHCHECK CMD curl -sf http://localhost:8545/api/v1/health`

**Security Audit:**
- [ ] Round 26: Sync protocol, peer discovery, metrics exposure
  - Focus: eclipse attack via sync, DHT poisoning, metrics information leak
- [ ] Round 27: Verification + full regression
- [ ] Muc tieu: 0 HIGH issues open

**Release:**
- [ ] config.py: VERSION = "0.10.0", PROTOCOL_VERSION = 6
- [ ] PROTOCOL.md v6: sync protocol, peer discovery messages
- [ ] CHANGELOG.md, FEATURES.md, README.md update
- [ ] openapi.yaml update (them 8 endpoints)

**Files thay doi:**
```
.github/workflows/ci.yml (moi)
.github/workflows/docker.yml (moi)
.github/workflows/release.yml (moi)
.github/workflows/security.yml (moi)
Dockerfile
.dockerignore (moi)
docker-compose.yml
qbit_network/config.py
docs/openapi.yaml
docs/PROTOCOL.md
tracker/CHANGELOG.md
tracker/FEATURES.md
tracker/AUDIT_LOG.md
README.md
CLAUDE.md
```

**Tests:** ~40 tests (CI config validation, Docker build test, health check)

## v0.10.0 Tong ket

| Metric | Truoc (v0.9.0) | Sau (v0.10.0) |
|--------|----------------|---------------|
| TX Types | 22 | 22 (khong doi) |
| Tests | ~2350 | ~2650 |
| Audit Rounds | 25 | 27 |
| Protocol Version | 5 | 6 |
| REST Endpoints | ~58 | ~66 |
| Deployment | Manual | Docker + CI/CD |
| Monitoring | None | Prometheus + Grafana |
| External Indexing | None | PostgreSQL bridge |
| Node Count (testnet) | 1 | 4 (3 validators + 1 full) |

## v0.10.0 Dependency Graph

```
Sprint 1 (Sync + Testnet)
    |
    +---> Sprint 2 (Discovery)  <-- can sync truoc
    |
    +---> Sprint 3 (Metrics + Postgres)  <-- doc lap voi sync
    |
    v
Sprint 4 (CI/CD + Audit)  <-- phu thuoc tat ca sprints truoc
```

Sprint 2 va Sprint 3 co the chay song song.

---

# v1.0.0 "Production Ready"

## Muc tieu

Chuan bi cho mainnet launch. External security audit, genesis configuration, key ceremony tooling, HSM support, disaster recovery, documentation portal, va performance optimization. Day la release chung chi — moi thu phai production-grade.

## TX Types moi

Khong co. v1.0.0 la stabilization + hardening release.

## API Endpoints moi

### REST

| Method | Path | Auth | Muc dich |
|--------|------|------|----------|
| GET | /api/v1/genesis | Public | Genesis block info + config |
| POST | /api/v1/admin/snapshot | Protected | Tao chain snapshot |
| GET | /api/v1/admin/snapshot/status | Protected | Trang thai snapshot |
| POST | /api/v1/admin/restore | Protected | Khoi phuc tu snapshot |
| GET | /api/v1/admin/hsm/status | Protected | HSM connection status |

### JSON-RPC (4 methods moi)

| Method | Auth | Muc dich |
|--------|------|----------|
| qv_getGenesis | Public | Genesis configuration |
| qv_createSnapshot | Protected | Tao snapshot |
| qv_getSnapshotStatus | Protected | Trang thai snapshot |
| qv_restoreSnapshot | Protected | Khoi phuc |

## Sprint Plan

### Sprint 1: Performance Optimization (Tuan 1-2)

**Agents:** `perf-engineer` (chinh), `blockchain-dev` (implementation)

**Muc tieu cu the:** 100+ TPS sustained, < 3s block processing at 200 TX/block.

**Deliverables:**

**Profiling + Benchmarking:**
- [ ] `benchmarks/` directory voi automated benchmark suite
- [ ] `bench_tx_validation.py`: measure TX validation throughput (target: 500+ TX/s)
- [ ] `bench_block_production.py`: measure block production time (target: < 2s cho 200 TX)
- [ ] `bench_state_trie.py`: measure state root computation (target: < 100ms cho 100K entries)
- [ ] `bench_sig_verify.py`: ML-DSA-65 verify throughput (target: 33+ ops/s per core)
- [ ] `bench_p2p_throughput.py`: P2P message throughput (target: 1000+ msg/s)
- [ ] CI integration: benchmark regression check on every PR

**Optimizations:**
- [ ] Batch signature verification: verify nhieu TX signatures trong 1 call (nop_oqs batching neu co)
- [ ] TX validation parallelism: `concurrent.futures.ProcessPoolExecutor` cho CPU-bound sig verify
  - Voi GIL relaxation (Python 3.13 free-threaded): ThreadPoolExecutor thay the
  - Target: 4x speedup tren 4-core machine
- [ ] State trie optimization: lazy root computation, incremental update thay vi rebuild
  - Hien tai: `_rebuild_state_trie()` rebuild toan bo sau moi block -> O(n) voi n = so accounts
  - Muc tieu: incremental update chi nhung entries thay doi -> O(k) voi k = so entries thay doi trong block
- [ ] SQLite WAL mode: `PRAGMA journal_mode=WAL` cho concurrent reads
- [ ] Memory pool: pre-allocate TX objects, reduce GC pressure
- [ ] P2P message batching: gom nhieu TX vao 1 message khi broadcasting

**Files thay doi:**
```
benchmarks/ (thu muc moi)
benchmarks/bench_tx_validation.py
benchmarks/bench_block_production.py
benchmarks/bench_state_trie.py
benchmarks/bench_sig_verify.py
benchmarks/bench_p2p_throughput.py
qbit_network/core/blockchain.py (incremental state trie)
qbit_network/core/state_tree.py (incremental update)
qbit_network/core/store.py (WAL mode)
qbit_network/core/tx_pool.py (parallel validation)
qbit_network/network/p2p.py (message batching)
tests/test_performance.py (moi)
```

**Tests:** ~30 tests (benchmark correctness, parallel validation consistency)

**Rui ro:**
- HIGH: Python GIL la bottleneck co ban. Free-threaded Python 3.13 chua stable. ProcessPoolExecutor co IPC overhead.
- Giai phap: Profile truoc, chi optimize hotspots. 100 TPS la kha thi voi batch processing ma khong can Rust rewrite.

---

### Sprint 2: HSM + Key Ceremony + Genesis (Tuan 3-4)

**Agents:** `security-auditor` (chinh), `devops` (HSM integration), `protocol-designer` (genesis design)

**Deliverables:**

**HSM Support (`qbit_network/crypto/hsm.py`):**
- [ ] Abstract `HSMProvider` interface:
  ```python
  class HSMProvider(ABC):
      async def sign(self, data: bytes) -> bytes: ...
      async def get_public_key(self) -> bytes: ...
      async def is_available(self) -> bool: ...
  ```
- [ ] `SoftHSM` implementation: default, dung in-memory keys (backward compatible)
- [ ] `PKCS11HSM` implementation: PKCS#11 interface cho hardware HSMs
  - Ho tro: YubiHSM 2, Thales Luna, AWS CloudHSM
  - Library: `python-pkcs11` (optional dependency)
  - Config: HSM_LIBRARY_PATH, HSM_SLOT, HSM_PIN env vars
  - Chi ho tro signing operations — key generation van manual
- [ ] Node integration: validator signing dung HSMProvider thay vi in-memory key
- [ ] `qbit_network/crypto/hsm_pkcs11.py`: PKCS#11 wrapper
- [ ] Fallback: neu HSM khong available, log WARNING va dung SoftHSM

**Key Ceremony Tooling (`tools/key_ceremony.py`):**
- [ ] CLI tool cho mainnet key generation ceremony:
  1. Generate N validator wallets (interactive, passphrase required)
  2. Generate genesis.json voi validator set + initial allocations
  3. Sign genesis block voi all validators (round-robin)
  4. Export public keys cho cross-verification
  5. Verify all signatures truoc khi finalize
- [ ] Multi-party ceremony protocol:
  - Step 1: Moi validator generate keypair offline
  - Step 2: Exchange public keys qua secure channel
  - Step 3: Coordinator tao genesis.json template
  - Step 4: Moi validator sign genesis template
  - Step 5: Coordinator aggregate signatures va distribute final genesis
- [ ] Output: `genesis.json`, `validator_pubkeys.json`, ceremony log

**Genesis Configuration (`qbit_network/core/genesis.py`):**
- [ ] `GenesisConfig` dataclass:
  ```python
  @dataclass
  class GenesisConfig:
      chain_id: str
      timestamp: str  # ISO 8601
      validators: list[ValidatorEntry]
      allocations: list[AllocationEntry]
      params: ChainParams
  ```
- [ ] `ValidatorEntry`: address, signing_pubkey, encryption_pubkey, initial_stake
- [ ] `AllocationEntry`: address, amount (qubits)
- [ ] `ChainParams`: block_interval, max_tx_per_block, epoch_length, ...
- [ ] Genesis validation: verify signatures, verify allocations <= MAX_SUPPLY * 10%, verify >= 3 validators
- [ ] `load_genesis(path)` va `create_genesis_block(config)` functions

**Files thay doi:**
```
qbit_network/crypto/hsm.py (moi)
qbit_network/crypto/hsm_pkcs11.py (moi)
qbit_network/core/genesis.py (moi)
tools/key_ceremony.py (moi)
tools/README.md (moi)
qbit_network/node.py (HSM integration)
qbit_network/core/blockchain.py (genesis from config)
qbit_network/core/consensus.py (HSM signing)
requirements-hsm.txt (moi: python-pkcs11)
tests/test_hsm.py (moi)
tests/test_genesis.py (moi)
tests/test_key_ceremony.py (moi)
```

**Tests:** ~50 tests (HSM mock, genesis validation, ceremony flow)

**Rui ro:**
- HIGH: PKCS#11 compatibility across HSM vendors. Giai phap: test voi SoftHSM2 emulator, abstract interface cho vendor differences.
- MEDIUM: Key ceremony la single point of failure. Giai phap: multi-party protocol, offline generation, verifiable ceremony log.

---

### Sprint 3: Disaster Recovery + Documentation Portal (Tuan 5-6)

**Agents:** `devops` (DR), `docs-writer` (portal), `frontend-dev` (docs site)

**Deliverables:**

**Disaster Recovery:**
- [ ] `tools/snapshot.py`: CLI tool cho chain snapshots
  - Full snapshot: SQLite database + chain metadata + state trie
  - Incremental snapshot: chi blocks moi tu last snapshot
  - Compress: gzip, target < 100MB cho 1M blocks
  - Verify: integrity check truoc restore
- [ ] `tools/restore.py`: restore tu snapshot
  - Validate snapshot integrity (SHA3-256 checksum)
  - Restore SQLite database
  - Rebuild in-memory state (balances, nonces, stakes, tokens, DIDs)
  - Verify state root match
- [ ] Disaster recovery runbook (`docs/DISASTER_RECOVERY.md`):
  - Scenario 1: Single validator down -> restart + sync tu peers
  - Scenario 2: Data corruption -> restore tu snapshot
  - Scenario 3: >1/3 validators down -> manual intervention, epoch skip
  - Scenario 4: Chain fork -> evaluate_fork + manual rollback
  - Scenario 5: Key compromise -> revoke key + re-stake voi new validator
- [ ] Automated backup: cron-compatible snapshot script
- [ ] Node: admin RPC cho snapshot/restore operations

**Documentation Portal:**
- [ ] MkDocs site (`docs-site/`) voi Material theme
- [ ] Structure:
  ```
  docs-site/
    mkdocs.yml
    docs/
      index.md (landing page)
      getting-started/
        installation.md
        quickstart.md
        configuration.md
      architecture/
        overview.md (tu ARCHITECTURE.md)
        consensus.md
        cryptography.md
        state-management.md
      protocol/
        specification.md (tu PROTOCOL.md)
        p2p-messages.md
        rpc-api.md
        rest-api.md
      guides/
        running-validator.md
        token-issuance.md
        did-management.md
        multisig-setup.md
        sdk-python.md
        sdk-typescript.md
      security/
        threat-model.md (tu SECURITY.md)
        audit-history.md (tu AUDIT_LOG.md)
        key-management.md
      operations/
        monitoring.md
        disaster-recovery.md
        upgrades.md
      reference/
        config.md
        tx-types.md
        error-codes.md
  ```
- [ ] GitHub Actions: deploy to GitHub Pages on tag push
- [ ] Search: built-in MkDocs search
- [ ] API docs: embed OpenAPI spec voi Swagger UI

**Files thay doi:**
```
tools/snapshot.py (moi)
tools/restore.py (moi)
docs/DISASTER_RECOVERY.md (moi)
docs-site/ (thu muc moi)
docs-site/mkdocs.yml
docs-site/docs/ (nhieu files)
.github/workflows/docs.yml (moi)
qbit_network/network/rpc.py (snapshot/restore RPC)
qbit_network/network/rest_api.py (snapshot/restore REST)
tests/test_snapshot.py (moi)
tests/test_restore.py (moi)
```

**Tests:** ~30 tests (snapshot integrity, restore correctness, incremental snapshot)

---

### Sprint 4: External Audit + Final Release (Tuan 7-8)

**Agents:** `security-auditor` (coordinate external audit), `test-runner` (final regression), `docs-writer` (release notes), `report-writer` (audit report)

**Deliverables:**

**External Security Audit Preparation:**
- [ ] Audit scope document: tat ca cryptographic operations, consensus logic, P2P protocol, RPC attack surface
- [ ] Code freeze: 1 tuan truoc audit, chi fix critical bugs
- [ ] Audit package: source code + architecture docs + threat model + test coverage report
- [ ] Cac firm de xuat: Trail of Bits, NCC Group, Cure53 (chon 1, budget ~$50-100K)
- [ ] Timeline: audit 2-3 tuan, response 1 tuan

**Internal Final Audit (Round 28-29):**
- [ ] Round 28: Full security audit — focus areas:
  - Cryptographic correctness: ML-DSA-65 signature verification, ML-KEM-768 key exchange
  - Consensus safety: fork resolution, epoch transition, finality
  - State consistency: trie root, rollback, persistence
  - Network security: P2P auth, rate limiting, eclipse attack resistance
  - RPC security: input validation, rate limiting, auth bypass
  - Token security: allowance, freeze, burn, multisig
  - DID security: controller escalation, deactivation bypass
- [ ] Round 29: Verification of R28 fixes + full regression

**Performance Verification:**
- [ ] Benchmark suite pass: 100+ TPS, < 3s block time, < 500MB memory at 100K blocks
- [ ] Load test: 24h continuous operation voi 10 TPS sustained
- [ ] Testnet soak test: 3-validator testnet chay 1 tuan lien tuc

**Release Checklist:**
- [ ] config.py: VERSION = "1.0.0", PROTOCOL_VERSION = 7
- [ ] CHAIN_ID chuyen tu "qbit-mainnet" (da set) -> verify chinh xac
- [ ] Genesis block configuration finalized
- [ ] PROTOCOL.md v7: final specification
- [ ] CHANGELOG.md: full v1.0.0 release notes
- [ ] FEATURES.md: complete feature list
- [ ] README.md: production badges, installation guide
- [ ] CLAUDE.md: update version + features
- [ ] Git tag: v1.0.0
- [ ] Docker image: ghcr.io/hongochai10/qbit-network:1.0.0
- [ ] npm publish: @qbit-network/sdk
- [ ] PyPI publish: qbit-sdk
- [ ] Documentation portal live
- [ ] GitHub Release voi release notes

**Files thay doi:**
```
qbit_network/config.py
docs/PROTOCOL.md
docs/openapi.yaml
tracker/CHANGELOG.md
tracker/FEATURES.md
tracker/AUDIT_LOG.md
README.md
CLAUDE.md
```

**Tests:** ~50 tests (regression, load test, soak test infrastructure)

## v1.0.0 Tong ket

| Metric | Truoc (v0.10.0) | Sau (v1.0.0) |
|--------|-----------------|-------------|
| TX Types | 22 | 22 (khong doi) |
| Tests | ~2650 | ~2900 |
| Audit Rounds | 27 | 29 + 1 external |
| Protocol Version | 6 | 7 |
| TPS (sustained) | ~40 | 100+ |
| Deployment | Docker | Docker + K8s-ready |
| Key Management | Software | Software + HSM |
| Documentation | Markdown files | Full portal + API docs |
| Snapshots | None | Full + incremental |
| External Audit | None | 1 (Trail of Bits / NCC Group / Cure53) |

## v1.0.0 Dependency Graph

```
Sprint 1 (Performance)  <-- doc lap
    |
Sprint 2 (HSM + Genesis)  <-- doc lap
    |
Sprint 3 (DR + Docs)  <-- doc lap, nhung tot hon neu co Sprint 2 (genesis docs)
    |
    v
Sprint 4 (External Audit + Release)  <-- phu thuoc tat ca sprints truoc
```

Sprint 1, 2, 3 co the chay song song neu co du nhan luc.

---

# Tong ket toan bo Roadmap

## Timeline

```
2026-04 |====== v0.9.0 Sprint 1-2 ======|
2026-05 |====== v0.9.0 Sprint 3-4 ======|
2026-06 |== v0.9.0 S5 ==|== v0.10.0 S1 ==|
2026-07 |===== v0.10.0 Sprint 2-3 ======|
2026-08 |== v0.10.0 S4 ==|== v1.0.0 S1 ==|
2026-09 |====== v1.0.0 Sprint 2-3 ======|
2026-10 |== v1.0.0 S4 (Audit + Release) ==|
```

## So lieu cuoi cung du kien

| Metric | v0.8.0 (hien tai) | v1.0.0 (muc tieu) |
|--------|-------------------|-------------------|
| TX Types | 14 | 22 |
| Tests | 1,781 | ~2,900 |
| Source Lines | ~11,350 | ~18,000 |
| Audit Rounds | 23 | 29 + 1 external |
| Issues Found/Fixed | 239+ | ~350+ |
| Protocol Version | 4 | 7 |
| REST Endpoints | ~42 | ~71 |
| JSON-RPC Methods | ~35 | ~57 |
| SDKs | Python | Python + TypeScript |
| Deployment | Manual | Docker + CI/CD + HSM |
| Documentation | Markdown | Full portal |
| TPS | ~40 | 100+ |
| Testnet | None | 3+ validators |

## Budget du kien (nhan luc)

| Version | Dev Effort | Audit Effort | Total |
|---------|------------|-------------|-------|
| v0.9.0 | 8 person-weeks | 2 person-weeks | 10 person-weeks |
| v0.10.0 | 6 person-weeks | 2 person-weeks | 8 person-weeks |
| v1.0.0 | 5 person-weeks | 3 person-weeks + external | 8 person-weeks + $50-100K |
| **Total** | **19 person-weeks** | **7 person-weeks** | **26 person-weeks + external audit** |

## Rui ro tong the

| Rui ro | Muc do | Giai phap |
|--------|--------|-----------|
| Python GIL gioi han TPS | HIGH | Batch processing, ProcessPoolExecutor. Rust rewrite la Phase 5 (post v1.0) |
| Multi-sig replay attack | HIGH | Nonce + chain_id + tx_hash trong signed payload |
| Chain sync eclipse attack | HIGH | Multi-peer download, header cross-verification |
| HSM vendor compatibility | MEDIUM | Abstract interface, test voi SoftHSM2 |
| External audit findings | MEDIUM | Code freeze 1 tuan truoc, reserve 2 tuan cho fixes |
| Allowance front-running | MEDIUM | Set-only approve (khong increment), transferFrom atomic deduction |
| DID controller escalation | MEDIUM | Strict owner-only deactivation, authentication key min=1 |
| Docker image size (liboqs) | LOW | Multi-stage build, target < 200MB |

## Dieu kien huy bo (Circuit Breakers)

Dung lai va re-evaluate neu:
1. External audit tim > 5 CRITICAL issues -> delay v1.0.0, fix truoc khi release
2. TPS < 50 sau optimization -> xem xet Rust rewrite cho hot path
3. Multi-node testnet khong stable sau 1 tuan soak test -> them sprint cho network layer
4. HSM integration khong kha thi voi Python -> dung SoftHSM only cho v1.0.0, defer hardware HSM

---

**Document version:** 1.0
**Last updated:** 2026-03-28
**Next review:** Sau moi sprint completion
