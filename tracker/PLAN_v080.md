# v0.8.0 "Enterprise Foundation" — Sprint Plan

**Start date:** 2026-03-28
**Features:** Multi-Asset Tokens, Light Client Protocol, Binary P2P (msgpack)
**Target:** 14 TX types, ~1857 tests, Protocol Version 4

---

## Overview

| Feature | New TX Types | New Tests | Sprint |
|---------|-------------|-----------|--------|
| Multi-Asset Tokens | ISSUE_TOKEN, MINT_TOKEN, TRANSFER_TOKEN | ~135 | 1-2 |
| Light Client Protocol | — (P2P messages + API) | ~80 | 3 |
| Binary P2P (msgpack) | — (wire format upgrade) | ~60 | 4 |
| Integration + Audit | — | ~75 | 5 |

---

## Sprint 1: Multi-Asset Tokens — Core (Week 1-2)

### New TX Types

| TX Type | Purpose | Fee (QBIT) | Weight |
|---------|---------|-----------|--------|
| ISSUE_TOKEN | Create new token class | 0.5 | 50,000,000 |
| MINT_TOKEN | Mint units to recipient | 0.01 | 1,000,000 |
| TRANSFER_TOKEN | Transfer custom tokens | 0.001 | 100,000 |

### Token Data Model

- **Token ID**: `SHA3-256(issuer + symbol + nonce)[:32].hex()` — 32 hex chars
- **Registry**: token_id -> {issuer, name, symbol, decimals, max_supply, total_minted, transferable, created_block, created_tx}
- **Balances**: (token_id, address) -> amount (integer)
- **Reserved symbol**: "QBIT" (native token)

### Payload Schemas

**ISSUE_TOKEN**: `{name, symbol, decimals, max_supply?, transferable?}`
- name: 1-64 chars, `^[a-zA-Z0-9 ]+$`
- symbol: 2-8 chars, `^[A-Z][A-Z0-9]+$`
- decimals: 0-18 integer
- max_supply: >= 0 (0 = unlimited), default 0
- transferable: bool, default True

**MINT_TOKEN**: `{token_id, amount}`
- token_id: 32 hex chars, must exist
- amount: > 0 integer
- recipient required, only issuer can mint
- Respects max_supply cap

**TRANSFER_TOKEN**: `{token_id, amount, memo?}`
- token_id: 32 hex chars, must exist
- amount: > 0 integer
- recipient required, != sender
- Token must be transferable
- Sender must hold >= amount

### Deliverables

- [x] TxType enum + _ALLOWED_KEYS for 3 new types
- [x] validate_payload() branches
- [x] Factory methods (issue_token, mint_token, transfer_token)
- [x] config.py: fees, weights, TOKEN_ACTIVATION_HEIGHT, token constants
- [x] blockchain.py: _token_registry, _token_balances state
- [x] blockchain.py: _append_block_inner branches for 3 types
- [x] query.py: get_token_info, list_tokens, get_token_holders, get_address_tokens
- [x] store.py: tokens + token_balances SQLite tables
- [x] persistence.py: load/save token state
- [x] rollback.py: reverse token operations
- [x] state_ops.py: token entries in state trie
- [x] receipt_ops.py: token_issued, token_minted, token_transferred events
- [x] REST API: 7 endpoints (4 public + 3 protected)
- [x] JSON-RPC: 7 methods (4 public + 3 protected)
- [x] WebSocket: token_event channel (TokenIssued, TokenMinted, TokenTransferred)
- [x] Unit tests: 125 token tests
- [x] All existing 1507 tests still pass

---

## Sprint 2: Light Client Protocol (Week 3-4)

### P2P Messages

| Message | Direction | Purpose |
|---------|-----------|---------|
| get_headers | Request | Block headers by range |
| headers | Response | Array of header objects |
| get_state_proof | Request | State proof for key |
| state_proof | Response | Key + value + Merkle proof |
| get_receipt_proof | Request | Receipt proof for TX |
| receipt_proof | Response | Receipt + Merkle proof |

### Deliverables

- [x] Block.to_header_dict() method
- [ ] P2P message handlers (deferred to Sprint 3 with binary protocol)
- [x] Receipt Merkle proof generation (receipt_proof + verify_receipt_proof)
- [x] REST: 4 endpoints (/headers, /headers/{index}, /proofs/state/{key}, /proofs/receipt/{txid})
- [x] JSON-RPC: 3 methods (qv_getBlockHeaders, qv_getStateProofAt, qv_getReceiptProof)
- [x] Historical state proof (via state snapshots within MAX_REORG_DEPTH)
- [x] get_state_proof_at_block() with snapshot-based trie restoration
- [x] Unit tests: 79 light client tests

---

## Sprint 3: Binary P2P Protocol (Week 5-6)

### Wire Format

```
[4-byte big-endian length][msgpack payload]
```

### Protocol Negotiation

- v4 peers include `wire_format: "msgpack"` in hello_auth
- Both v4 -> msgpack; v4+v3 -> JSON fallback
- Binary fields: signature, pubkeys, challenges -> raw bytes

### Deliverables

- [x] codec.py: MessageCodec (JSON + msgpack backends)
- [x] P2P: length-prefixed binary framing (4-byte big-endian + msgpack payload)
- [x] Protocol negotiation in hello_auth + auth_response
- [x] Per-peer wire_format tracking (Peer.__slots__)
- [x] Hex-to-bytes field conversion (_BINARY_FIELDS: signature, pubkeys, challenges)
- [x] Wire format switch after encrypted channel setup (not during handshake)
- [x] Unit tests: 70 codec tests

---

## Sprint 4: Integration, Audit, Release (Week 7-8)

### Deliverables

- [x] Security audit Round 21 — 11 issues (3 HIGH, 5 MED, 3 LOW), 10 fixed
- [x] Round 22 verification audit — all R21 fixes confirmed
- [x] Token pool admission hardening (symbol, issuer, balance checks)
- [x] SQLite token rollback cleanup
- [x] PROTOCOL.md v4 update (tokens, light client, binary P2P, weight table)
- [x] CHANGELOG.md v0.8.0 entries (4 sprints)
- [x] FEATURES.md update (4 sprints)
- [x] Agent definitions update (9 files)
- [x] README.md badges, TX count, audit count synced
- [x] CLAUDE.md version and features updated

---

## Config Changes

```python
VERSION = "0.8.0"
PROTOCOL_VERSION = 4
TOKEN_ACTIVATION_HEIGHT = 0
MAX_TOKEN_NAME_LENGTH = 64
MAX_TOKEN_SYMBOL_LENGTH = 8
MIN_TOKEN_SYMBOL_LENGTH = 2
MAX_TOKEN_DECIMALS = 18

TX_FEES["ISSUE_TOKEN"] = 500_000_000      # 0.5 QBIT
TX_FEES["MINT_TOKEN"] = 10_000_000        # 0.01 QBIT
TX_FEES["TRANSFER_TOKEN"] = 1_000_000     # 0.001 QBIT

TX_WEIGHTS["ISSUE_TOKEN"] = 50_000_000
TX_WEIGHTS["MINT_TOKEN"] = 1_000_000
TX_WEIGHTS["TRANSFER_TOKEN"] = 100_000
```

## Key Decisions

1. Token ID = truncated SHA3-256 (not sequential) — deterministic, collision-resistant
2. Issuer-only minting — simple auth model for v0.8.0
3. No token burning — defer BURN_TOKEN to v0.9.0
4. Msgpack optional — JSON always fallback
5. Light client trusts checkpoint — trustless bootstrap deferred
6. W3C DID + TypeScript SDK -> v0.9.0
