# REST API Guide

## Base URL

```
http://localhost:8545/api/v1/
```

Replace `localhost:8545` with your node's host and port.

## Authentication

Write operations (creating wallets, submitting transactions) require a Bearer token:

```
Authorization: Bearer YOUR_RPC_TOKEN
```

The token is printed at node startup. Public read operations require no authentication.

## Response Format

All responses use the same envelope:

```json
{"data": <result>, "error": null}
```

On error:

```json
{"data": null, "error": {"code": 404, "message": "block not found"}}
```

HTTP status codes are also set: 200 for success, 400 for bad request, 401 for missing/invalid token, 404 for not found, 429 for rate limit exceeded.

## Pagination

Paginated endpoints accept `page` (1-based, default 1) and `limit` (default 20, max 100):

```
GET /api/v1/blocks?page=2&limit=50
```

---

## Public Endpoints

### Health check

```
GET /api/v1/health
```

```bash
curl http://localhost:8545/api/v1/health
```

```json
{"data": {"status": "ok"}, "error": null}
```

### Node information

```
GET /api/v1/info
```

```bash
curl http://localhost:8545/api/v1/info
```

```json
{
  "data": {
    "version": "0.6.0",
    "chain_id": "qbit-mainnet",
    "height": 150,
    "peers": 2,
    "wallets": 1,
    "pool_size": 3
  },
  "error": null
}
```

### List blocks (paginated)

```
GET /api/v1/blocks?page=1&limit=20
```

```bash
curl "http://localhost:8545/api/v1/blocks?page=1&limit=5"
```

```json
{
  "data": {
    "blocks": [
      {
        "index": 150,
        "hash": "a1b2c3...",
        "prevHash": "f9e8d7...",
        "timestamp": 1711458130,
        "validator": "qv1abc...",
        "txCount": 2
      }
    ],
    "total": 151,
    "page": 1,
    "limit": 5
  },
  "error": null
}
```

### Latest block

```
GET /api/v1/blocks/latest
```

```bash
curl http://localhost:8545/api/v1/blocks/latest
```

Returns the full block object including transactions.

### Block by index

```
GET /api/v1/blocks/:index
```

```bash
curl http://localhost:8545/api/v1/blocks/42
```

```json
{
  "data": {
    "index": 42,
    "hash": "a1b2c3...",
    "prevHash": "f9e8d7...",
    "merkleRoot": "11223344...",
    "timestamp": 1711458000,
    "validator": "qv1abc...",
    "signature": "3a8f...",
    "transactions": [
      {
        "id": "7f8e9d...",
        "type": "NOTARIZE",
        "from": "qv1abc...",
        "timestamp": 1711457990,
        "payload": {"documentHash": "deadbeef...", "metadata": "contract.pdf"}
      }
    ]
  },
  "error": null
}
```

### Block by hash

```
GET /api/v1/blocks/hash/:hash
```

```bash
curl http://localhost:8545/api/v1/blocks/hash/a1b2c3d4...
```

### Transaction by ID

```
GET /api/v1/txs/:txid
```

```bash
curl http://localhost:8545/api/v1/txs/7f8e9d0a1b2c...
```

```json
{
  "data": {
    "id": "7f8e9d0a...",
    "type": "TRANSFER",
    "from": "qv1abc...",
    "to": "qv1def...",
    "timestamp": 1711458000,
    "nonce": 3,
    "payload": {"amount": 1000000000, "memo": "payment"},
    "block_index": 42
  },
  "error": null
}
```

### Transactions by sender

```
GET /api/v1/txs/sender/:addr?page=1&limit=20
```

```bash
curl "http://localhost:8545/api/v1/txs/sender/qv1abc...?limit=10"
```

### Address summary

```
GET /api/v1/address/:addr
```

```bash
curl http://localhost:8545/api/v1/address/qv1abc...
```

```json
{
  "data": {
    "address": "qv1abc...",
    "balance": 2100000000000000,
    "tx_count": 5,
    "nonce": 4
  },
  "error": null
}
```

Balance is in qubits (1 QBIT = 1,000,000,000 qubits).

### Notarizations for a document hash

```
GET /api/v1/notarizations/:hash
```

```bash
curl http://localhost:8545/api/v1/notarizations/deadbeef...
```

Returns all NOTARIZE transactions that reference this document hash, across any block.

### Validators

```
GET /api/v1/validators
```

```bash
curl http://localhost:8545/api/v1/validators
```

```json
{
  "data": [
    {
      "address": "qv1abc...",
      "total_stake": 1000,
      "slashed": false
    }
  ],
  "error": null
}
```

### Transaction pool

```
GET /api/v1/pool?page=1&limit=20
GET /api/v1/pool/count
```

```bash
curl http://localhost:8545/api/v1/pool/count
```

```json
{"data": {"count": 3}, "error": null}
```

### Verify document hash

```
POST /api/v1/verify
Body: {"document_hash": "<sha3-256 hex>"}
```

```bash
curl -X POST http://localhost:8545/api/v1/verify \
  -H "Content-Type: application/json" \
  -d '{"document_hash": "deadbeef..."}'
```

```json
{
  "data": {
    "document_hash": "deadbeef...",
    "tx_id": "7f8e9d...",
    "block_index": 42,
    "timestamp": 1711458000,
    "sender": "qv1abc..."
  },
  "error": null
}
```

Returns `{"data": null, "error": {"code": 404, ...}}` if the hash is not found.

### Stake information

```
GET /api/v1/stakes
GET /api/v1/stakes/:validator
```

```bash
curl http://localhost:8545/api/v1/stakes/qv1abc...
```

### Current epoch

```
GET /api/v1/epochs/current
```

```bash
curl http://localhost:8545/api/v1/epochs/current
```

```json
{
  "data": {
    "epoch_number": 3,
    "start_block": 200,
    "end_block": 299,
    "validators": ["qv1abc...", "qv1def..."],
    "active_validators": 2
  },
  "error": null
}
```

### Slashing events

```
GET /api/v1/slashing-events
```

```bash
curl http://localhost:8545/api/v1/slashing-events
```

### Current fee

```
GET /api/v1/fee
```

```bash
curl http://localhost:8545/api/v1/fee
```

```json
{
  "data": {
    "base_fee": 10,
    "min_base_fee": 1,
    "max_base_fee": 10000
  },
  "error": null
}
```

---

## Protected Endpoints

All protected endpoints require `Authorization: Bearer YOUR_RPC_TOKEN`.

### Create wallet

```
POST /api/v1/wallets
Body: {"password": "your-password"}
```

```bash
curl -X POST http://localhost:8545/api/v1/wallets \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"password": "strong-password"}'
```

```json
{
  "data": {
    "address": "qv1abc...",
    "signing_pk": "3a8f...",
    "encryption_pk": "7c2d..."
  },
  "error": null
}
```

### List wallets

```
GET /api/v1/wallets
```

```bash
curl http://localhost:8545/api/v1/wallets \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### Submit NOTARIZE transaction

```
POST /api/v1/notarize
Body: {"wallet": "<address>", "document_hash": "<sha3-256 hex>", "metadata": "<optional>"}
```

```bash
curl -X POST http://localhost:8545/api/v1/notarize \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "wallet": "qv1abc...",
    "document_hash": "deadbeef...",
    "metadata": "contract.pdf"
  }'
```

```json
{"data": {"tx_id": "7f8e9d..."}, "error": null}
```

### Submit STORE transaction

```
POST /api/v1/store
Body: {"wallet": "<address>", "document_hash": "<hex>", "cid": "<ipfs-cid>", "metadata": "<optional>"}
```

```bash
curl -X POST http://localhost:8545/api/v1/store \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "wallet": "qv1abc...",
    "document_hash": "deadbeef...",
    "cid": "QmXyz123...",
    "metadata": "document.pdf"
  }'
```

### Submit SHARE transaction

```
POST /api/v1/share
Body: {"wallet": "<address>", "recipient": "<address>", "cid": "<ipfs-cid>", "encapsulated_key": "<hex>"}
```

The `encapsulated_key` is the ML-KEM-768 ciphertext — the node computes this from the recipient's on-chain encryption key. If you call this endpoint directly, the node handles encapsulation automatically given the recipient address.

### Submit TRANSFER transaction

```
POST /api/v1/transfer
Body: {"wallet": "<address>", "to": "<address>", "amount": <qubits>, "memo": "<optional>"}
```

```bash
curl -X POST http://localhost:8545/api/v1/transfer \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "wallet": "qv1abc...",
    "to": "qv1def...",
    "amount": 1000000000,
    "memo": "invoice 42"
  }'
```

### Register validator

```
POST /api/v1/register-validator
Body: {"wallet": "<address>"}
```

### Stake

```
POST /api/v1/stake
Body: {"wallet": "<address>", "validator_address": "<your-address>", "amount": <integer>}
```

### Delegate

```
POST /api/v1/delegate
Body: {"wallet": "<address>", "validator_address": "<validator>", "amount": <integer>}
```

### Unstake

```
POST /api/v1/unstake
Body: {"wallet": "<address>", "validator_address": "<validator>", "amount": <integer>}
```

### Submit EVIDENCE transaction

```
POST /api/v1/evidence
Body: {
  "wallet": "<reporter-address>",
  "validator_address": "<accused-validator>",
  "block_index": <integer>,
  "block_hash_1": "<hex>",
  "block_hash_2": "<hex>",
  "signature_1": "<ML-DSA hex>",
  "signature_2": "<ML-DSA hex>"
}
```

---

## WebSocket

Subscribe to real-time events at `ws://localhost:8545/ws`:

```javascript
const ws = new WebSocket('ws://localhost:8545/ws');
ws.onopen = () => {
  ws.send(JSON.stringify({"action": "subscribe", "channel": "new_block"}));
  ws.send(JSON.stringify({"action": "subscribe", "channel": "chain_stats"}));
};
ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  console.log(msg.channel, msg.data);
};
```

Available channels: `new_block`, `new_tx`, `chain_stats` (broadcasts every 5s).

Keepalive: send `{"action": "ping"}`, receive `{"action": "pong"}`.

## JSON-RPC 2.0

Alternatively, use the JSON-RPC interface at `POST http://localhost:8545`:

```bash
curl http://localhost:8545 \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"qv_nodeInfo","params":{},"id":1}'
```

See the README for the full list of JSON-RPC methods.

## Next Steps

- [Fee calculation details](08-fees.md)
- [Testnet setup](09-testnet.md)
- [Full protocol specification](../docs/PROTOCOL.md)
