# Fee System (EIP-1559)

## What are Transaction Fees?

Transaction fees compensate the validator who produces the block containing your transaction. Fees also serve as spam prevention — without them, anyone could flood the network with worthless transactions at no cost.

QBit uses an **EIP-1559-style dynamic fee system**, similar to Ethereum's post-London fee mechanism. The key idea is that the minimum fee (base fee) adjusts automatically based on how busy the network is, rather than relying on users to manually bid.

## Fixed Fees vs Dynamic Fees

Earlier QBit versions used fixed fees per transaction type. After the EIP-1559 activation (configurable via `DYNAMIC_FEE_ACTIVATION_HEIGHT`), the system switches to dynamic fees.

**Fixed fee mode (legacy, pre-activation):**
- Each transaction type has a flat fee in qubits
- 50% goes to the validator, 50% is burned
- No base fee concept

**Dynamic fee mode (EIP-1559, post-activation):**
- A `baseFee` is computed for every block based on recent utilization
- 100% of fees go to the block-producing validator (nothing burned)
- Users specify `maxFeePerWeight` (ceiling) and `maxPriorityFee` (tip)
- Transactions priced below the current base fee are rejected immediately

New chains start with dynamic fees active from genesis (`DYNAMIC_FEE_ACTIVATION_HEIGHT = 0`).

## How the Base Fee Works

Each block has a target utilization of **50%** of maximum block weight (`TARGET_BLOCK_WEIGHT = 10,000,000`). The maximum block weight is `MAX_BLOCK_WEIGHT = 20,000,000`.

After each block is finalized, the next block's base fee is computed:

```
if this_block_weight == TARGET_BLOCK_WEIGHT:
    next_base_fee = current_base_fee            # no change

elif this_block_weight > TARGET_BLOCK_WEIGHT:
    delta = this_block_weight - TARGET_BLOCK_WEIGHT
    increase = max(1, current_base_fee * delta / TARGET / 8)
    next_base_fee = current_base_fee + increase  # fee goes UP

else:
    delta = TARGET_BLOCK_WEIGHT - this_block_weight
    decrease = current_base_fee * delta / TARGET / 8
    next_base_fee = current_base_fee - decrease  # fee goes DOWN

next_base_fee = clamp(next_base_fee, 1, 10000)
```

The divisor 8 (`BASE_FEE_CHANGE_DENOM`) limits the maximum change per block to +12.5% or -12.5%. This prevents sudden fee spikes and gives users predictability.

**Base fee range:**
- Minimum: 1 qubit per weight unit (`MIN_BASE_FEE`)
- Maximum: 10,000 qubits per weight unit (`MAX_BASE_FEE`)
- Starting value: 10 qubits per weight unit (`INITIAL_BASE_FEE`)

## Transaction Weight

Each transaction type has a fixed weight. The weight reflects how much computational and storage cost the transaction imposes on the network.

| Transaction Type | Weight | Fee at base_fee=10 | Fee at base_fee=100 |
|-----------------|--------|-------------------|---------------------|
| TRANSFER | 100,000 | 1,000,000 qubits (0.001 QBIT) | 10,000,000 qubits (0.01 QBIT) |
| NOTARIZE | 1,000,000 | 10,000,000 qubits (0.01 QBIT) | 100,000,000 qubits (0.1 QBIT) |
| STORE | 2,000,000 | 20,000,000 qubits (0.02 QBIT) | 200,000,000 qubits (0.2 QBIT) |
| SHARE | 1,000,000 | 10,000,000 qubits (0.01 QBIT) | 100,000,000 qubits (0.1 QBIT) |
| REGISTER_KEY | 10,000,000 | 100,000,000 qubits (0.1 QBIT) | 1,000,000,000 qubits (1 QBIT) |
| REGISTER_VALIDATOR | 100,000,000 | 1,000,000,000 qubits (1 QBIT) | 10,000,000,000 qubits (10 QBIT) |
| STAKE / DELEGATE / UNSTAKE | 1,000,000 | 10,000,000 qubits (0.01 QBIT) | 100,000,000 qubits (0.1 QBIT) |
| REVOKE_KEY | 0 | 0 (free) | 0 (free) |
| EVIDENCE | 0 | 0 (free) | 0 (free) |

REGISTER_VALIDATOR is intentionally expensive (100x NOTARIZE) to prevent spamming the validator registry. REVOKE_KEY and EVIDENCE are free because the network benefits from their submission.

## Fee Formula

The fee you actually pay is:

```
effective_priority = min(maxPriorityFee, maxFeePerWeight - baseFee)
fee = (baseFee + effective_priority) * weight
```

Where:
- `baseFee` — current block's base fee (visible at `GET /api/v1/fee`)
- `maxFeePerWeight` — the maximum fee per weight unit you are willing to pay (your ceiling)
- `maxPriorityFee` — the tip you offer to the validator on top of the base fee
- `weight` — the fixed weight of your transaction type

Your `maxFeePerWeight` must be at least equal to the current `baseFee`, or the transaction is rejected by the pool immediately.

## Priority Fee (Tips)

The priority fee is an optional tip to the validator. A higher tip incentivizes the validator to include your transaction sooner when the pool is full.

For most use cases, a priority fee of 0-10 qubits/weight is sufficient. When the pool is empty, any valid transaction is included in the next block regardless of priority.

## Example Calculations

**Normal conditions (base_fee = 10):**

```
TRANSFER:
  fee = (10 + 5) * 100,000 = 1,500,000 qubits = 0.0015 QBIT

NOTARIZE:
  fee = (10 + 0) * 1,000,000 = 10,000,000 qubits = 0.01 QBIT
```

**High congestion (base_fee = 100):**

```
TRANSFER:
  fee = (100 + 10) * 100,000 = 11,000,000 qubits = 0.011 QBIT

NOTARIZE:
  fee = (100 + 5) * 1,000,000 = 105,000,000 qubits = 0.105 QBIT
```

**Low activity (base_fee = 1):**

```
TRANSFER:
  fee = (1 + 0) * 100,000 = 100,000 qubits = 0.0001 QBIT

NOTARIZE:
  fee = (1 + 0) * 1,000,000 = 1,000,000 qubits = 0.001 QBIT
```

## Where Do Fees Go?

After EIP-1559 activation, 100% of all transaction fees go to the validator who produced the block. Nothing is burned. This is a change from the legacy fixed-fee model, where 50% was burned.

The validator's fee income is in addition to the block reward (5 QBIT per block, halving every 2,100,000 blocks).

## Anti-Spam Rules

Several rules prevent fee manipulation:

**Pool admission gate:** Transactions with `maxFeePerWeight < current_base_fee` are rejected before entering the pool. This prevents cheap transactions from accumulating and forcing validators to discard them.

**Validator self-transaction cap:** A validator can include their own transactions in a block, but those self-transactions are excluded from the effective weight calculation used to adjust the base fee. Additionally, the total weight of validator self-transactions is capped at 25% of the block weight. This prevents a validator from artificially inflating the base fee by filling blocks with their own transactions.

**Block weight limit:** A block cannot exceed `MAX_BLOCK_WEIGHT = 20,000,000`. Validators that try to produce overweight blocks will have them rejected by other nodes.

## Checking the Current Fee

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

## Setting Fee Fields in Transactions

When submitting transactions via the REST API:

```bash
curl -X POST http://localhost:8545/api/v1/transfer \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "wallet": "qv1abc...",
    "to": "qv1def...",
    "amount": 1000000000,
    "maxFeePerWeight": 20,
    "maxPriorityFee": 5
  }'
```

If you omit `maxFeePerWeight` and `maxPriorityFee`, the node uses safe defaults based on the current base fee. For most users, letting the node choose defaults is the simplest option.

## QBIT Token Units

| Unit | Value | Typical Use |
|------|-------|------------|
| qubit | 1 (smallest unit) | Fee calculations |
| QBIT | 1,000,000,000 qubits | Display, transfers |

When reading balances from the API, amounts are always in qubits. Divide by 1,000,000,000 (or 10^9) to convert to QBIT.

## Next Steps

- [Run a testnet to see fee changes in action](09-testnet.md)
- [Security model](10-security.md)
- [Protocol specification for the fee adjustment formula](../docs/PROTOCOL.md)
