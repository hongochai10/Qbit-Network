# Wallets and Transfers

## What is a Wallet in QBit?

A QBit wallet contains two independent post-quantum keypairs:

| Keypair | Algorithm | Purpose |
|---------|-----------|---------|
| Signing keypair | ML-DSA-65 | Signs transactions and blocks |
| Encryption keypair | ML-KEM-768 | Receives encrypted files via SHARE transactions |

Your wallet address is derived from the signing public key:

```
address = "qv1" + hex(SHA3-256(signing_public_key))
```

This produces a 67-character address: the 3-character prefix `qv1` followed by 64 hex digits. The address is a one-way derivation — the public key cannot be recovered from the address.

Wallet files are stored as JSON and encrypted with AES-256-GCM using a key derived from your password via scrypt. The key material is zeroed from memory when the wallet is closed (using `SecureBytes` backed by ctypes).

## Create a Wallet

### Via CLI

```bash
python3 cli/qbit.py wallet create
```

You will be prompted for a password. To skip password encryption (for testing only):

```bash
python3 cli/qbit.py wallet create --no-password
```

Expected output:

```
Wallet created: qv1a3f9e2b...c4d1
Saved to: /Users/yourname/.qbit/wallets/qv1a3f9e2b...c4d1.json
(encrypted with password)
```

To create a wallet and immediately register its encryption key on-chain (required before others can send you encrypted files):

```bash
python3 cli/qbit.py wallet create --register --token YOUR_RPC_TOKEN
```

### Via REST API

```bash
curl -X POST http://localhost:8545/api/v1/wallets \
  -H "Authorization: Bearer YOUR_RPC_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"password": "your-wallet-password"}'
```

Expected response:

```json
{
  "data": {
    "address": "qv1a3f9e2b...c4d1",
    "signing_pk": "3a8f...",
    "encryption_pk": "7c2d..."
  },
  "error": null
}
```

### Via the Web Dashboard

1. Open the NextJS dashboard at `http://localhost:3000`
2. Navigate to the Wallets page
3. Click "Create Wallet"
4. Enter a password and confirm
5. Your address appears immediately

### List Your Wallets

```bash
python3 cli/qbit.py wallet list
```

Or via API:

```bash
curl http://localhost:8545/api/v1/wallets \
  -H "Authorization: Bearer YOUR_RPC_TOKEN"
```

## Check a Balance

```bash
curl http://localhost:8545/api/v1/address/qv1YOUR_ADDRESS
```

Expected response:

```json
{
  "data": {
    "address": "qv1a3f9e2b...c4d1",
    "balance": 2100000000000000,
    "tx_count": 1,
    "nonce": 0
  },
  "error": null
}
```

Balances are stored in **qubits**, the smallest unit. 1 QBIT = 1,000,000,000 qubits (9 decimal places). The genesis validator starts with 2,100,000 QBIT = 2,100,000,000,000,000 qubits.

## Send QBIT

To send QBIT, the sender wallet must be loaded on the node (the node holds the private key used to sign the transaction).

### Via REST API

```bash
curl -X POST http://localhost:8545/api/v1/transfer \
  -H "Authorization: Bearer YOUR_RPC_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "wallet": "qv1SENDER_ADDRESS",
    "to": "qv1RECIPIENT_ADDRESS",
    "amount": 1000000000,
    "memo": "payment for services"
  }'
```

The `amount` is in qubits. This example sends 1 QBIT (1,000,000,000 qubits).

Expected response:

```json
{
  "data": {
    "tx_id": "a1b2c3d4..."
  },
  "error": null
}
```

### Via the Web Dashboard

1. Go to the Transfer page in the NextJS dashboard
2. Enter the recipient's `qv1` address
3. Enter the amount in QBIT
4. Optionally add a memo (max 256 characters)
5. Set a priority fee if you want faster inclusion (see [fee guide](08-fees.md))
6. Click "Send"

The dashboard shows the transaction ID and confirms when the transaction is mined.

### Via curl (full example with fee fields)

```bash
# Check the current base fee first
curl http://localhost:8545/api/v1/fee

# Then submit with appropriate fee fields
curl -X POST http://localhost:8545/api/v1/transfer \
  -H "Authorization: Bearer YOUR_RPC_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "wallet": "qv1SENDER_ADDRESS",
    "to": "qv1RECIPIENT_ADDRESS",
    "amount": 5000000000,
    "maxFeePerWeight": 20,
    "maxPriorityFee": 5
  }'
```

The `maxFeePerWeight` and `maxPriorityFee` fields are optional. If omitted, the node uses safe defaults based on the current base fee.

## Understanding Fees

QBit uses an EIP-1559-style dynamic fee system. Each transaction type has a fixed **weight** (a measure of how much block space it consumes). The total fee is:

```
fee = (base_fee + effective_priority) * weight
```

For a TRANSFER transaction (weight = 100,000):
- At base_fee = 10 qubits/weight: fee = 10 * 100,000 = 1,000,000 qubits (0.001 QBIT)
- At base_fee = 100 qubits/weight: fee = 100 * 100,000 = 10,000,000 qubits (0.01 QBIT)

The base fee adjusts automatically based on block congestion. See [08-fees.md](08-fees.md) for the full calculation.

## Transaction Lifecycle

After you submit a transaction:

1. **Pool** — The transaction enters the pending transaction pool. You can see it at `GET /api/v1/pool`.
2. **Mined** — The next block producer includes it in a block. Blocks are produced every ~5 seconds when the pool is non-empty.
3. **Confirmed** — Once in a block, the transaction is confirmed. The block height it landed in is visible in the transaction record.

Check a transaction's status:

```bash
curl http://localhost:8545/api/v1/txs/YOUR_TX_ID
```

Expected response (confirmed):

```json
{
  "data": {
    "id": "a1b2c3d4...",
    "type": "TRANSFER",
    "from": "qv1SENDER...",
    "to": "qv1RECIPIENT...",
    "timestamp": 1700000000,
    "payload": {
      "amount": 1000000000,
      "memo": "payment for services"
    },
    "block_index": 42
  },
  "error": null
}
```

The `block_index` field is present once the transaction has been mined. If the transaction is still in the pool, `block_index` will be absent or null.

## Validation Rules

The following will cause a transfer to be rejected:

- Sending to an invalid address (must be `qv1` + 64 hex characters)
- Sending to yourself (`to == from`)
- Insufficient balance (amount + fee exceeds available balance)
- Amount is zero or negative
- Memo exceeds 256 characters

## Next Steps

- [Notarize a document](03-notarization.md)
- [Stake QBIT and earn rewards](04-staking.md)
- [Understand the fee system in depth](08-fees.md)
