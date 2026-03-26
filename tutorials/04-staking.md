# Staking and Validators

## What is Delegated Proof of Stake?

QBit Network uses Delegated Proof of Stake (dPoS) to determine which node produces each block. Instead of solving computational puzzles (Proof of Work), validators lock up stake (QBIT tokens) as collateral. The network selects which validator produces each block based on stake weight — validators with more stake are chosen more often.

"Delegated" means token holders who do not run validator nodes can still participate by delegating their stake weight to a validator they trust. Delegators share in the validator's block rewards proportionally.

The key properties of QBit's dPoS:

- **Stake-weighted selection** — each block's producer is chosen deterministically using `SHA3-256(parent_hash:block_index)` as a random seed over a cumulative stake distribution
- **Epoch-based rotation** — the active validator set is frozen for 100 blocks (one epoch), then updated with any stake changes
- **Slashing** — validators who double-sign are penalized by losing 50% of all staked weight
- **Unbonding period** — unstaked tokens are locked for 100 blocks before becoming available

## Step 1 — Register as a Validator

Before staking, you must register your validator key on-chain. This publishes your ML-DSA-65 public key so other nodes can verify your block signatures.

```bash
curl -X POST http://localhost:8545/api/v1/register-validator \
  -H "Authorization: Bearer YOUR_RPC_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"wallet": "qv1YOUR_ADDRESS"}'
```

Expected response:

```json
{
  "data": {
    "tx_id": "a1b2c3d4...",
    "validator_address": "qv1YOUR_ADDRESS"
  },
  "error": null
}
```

Wait for the next block to mine this transaction before proceeding.

Note: When you start a node with `python3 run_node.py`, the validator is registered automatically in the genesis block. This step is only needed when adding a new validator to an existing chain.

## Step 2 — Stake on Your Validator

Self-staking sets your validator's weight in the dPoS selection. The minimum stake is 1, the maximum is 1,000,000.

```bash
curl -X POST http://localhost:8545/api/v1/stake \
  -H "Authorization: Bearer YOUR_RPC_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "wallet": "qv1YOUR_ADDRESS",
    "validator_address": "qv1YOUR_ADDRESS",
    "amount": 1000
  }'
```

Expected response:

```json
{
  "data": {
    "tx_id": "b2c3d4e5...",
    "staked_amount": 1000,
    "validator": "qv1YOUR_ADDRESS"
  },
  "error": null
}
```

The `validator_address` must be your own address for a STAKE transaction (self-stake only). To stake on behalf of another validator, use DELEGATE (see below).

## Step 3 — Start Producing Blocks

Once your stake is mined and the next epoch boundary is reached (every 100 blocks), your validator enters the active set and begins producing blocks. The node handles block production automatically.

You can confirm your validator is active:

```bash
curl http://localhost:8545/api/v1/validators
```

Expected response:

```json
{
  "data": [
    {
      "address": "qv1YOUR_ADDRESS",
      "total_stake": 1000,
      "slashed": false
    }
  ],
  "error": null
}
```

## Delegate to a Validator

If you hold QBIT but do not want to run a validator node, you can delegate your stake weight to an existing validator. You earn a share of the block rewards proportional to your delegation, minus the validator's commission.

```bash
curl -X POST http://localhost:8545/api/v1/delegate \
  -H "Authorization: Bearer YOUR_RPC_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "wallet": "qv1YOUR_WALLET_ADDRESS",
    "validator_address": "qv1VALIDATOR_ADDRESS",
    "amount": 500
  }'
```

Expected response:

```json
{
  "data": {
    "tx_id": "c3d4e5f6...",
    "delegated_amount": 500,
    "validator": "qv1VALIDATOR_ADDRESS"
  },
  "error": null
}
```

## Unstake

To stop staking or delegating, submit an UNSTAKE transaction. The staked amount enters an **unbonding period** of 100 blocks. After the unbonding period, the tokens are returned to your balance automatically.

```bash
curl -X POST http://localhost:8545/api/v1/unstake \
  -H "Authorization: Bearer YOUR_RPC_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "wallet": "qv1YOUR_ADDRESS",
    "validator_address": "qv1VALIDATOR_ADDRESS",
    "amount": 500
  }'
```

Expected response:

```json
{
  "data": {
    "tx_id": "d4e5f6a7...",
    "unbonding_amount": 500,
    "release_block": 242
  },
  "error": null
}
```

`release_block` is the block index at which the tokens become available (current height + 100).

The unbonding period exists to prevent validators from withdrawing stake immediately after misbehaving. It gives the network time to process slashing evidence.

## Epoch Rotation

The validator set is frozen in **epochs** of 100 blocks. At the start of each epoch, the network:

1. Records a snapshot of the current stake state
2. Selects the active validators for that epoch based on who has stake
3. Distributes accumulated block rewards to delegators

Stake changes (new stakes, delegations, unstakes) take effect at the next epoch boundary. If you stake during epoch N, your stake is active for block production starting in epoch N+1.

Check the current epoch:

```bash
curl http://localhost:8545/api/v1/epochs/current
```

Expected response:

```json
{
  "data": {
    "epoch_number": 3,
    "start_block": 200,
    "end_block": 299,
    "validators": ["qv1ADDR1...", "qv1ADDR2..."],
    "active_validators": 2
  },
  "error": null
}
```

## Rewards

Validators earn two types of income:

**Block rewards** — 5 QBIT per block (halving every 2,100,000 blocks), credited to the block-producing validator. Block rewards decrease geometrically with each halving, similar to Bitcoin.

**Fee income** — 100% of transaction fees from transactions included in a block go to the block-producing validator (after the EIP-1559 dynamic fee activation).

At each epoch boundary (every 100 blocks), accumulated block rewards are redistributed to delegators:

- The validator keeps their **commission rate** (default 10%, configurable 0-100%)
- The remaining pool is distributed proportionally to all stakers by their stake weight

For example: if a validator earned 500 QBIT in an epoch with 10% commission and two equal delegators:
- Validator commission: 50 QBIT
- Each delegator: 225 QBIT

View stake information for a validator:

```bash
curl http://localhost:8545/api/v1/stakes/qv1VALIDATOR_ADDRESS
```

## Slashing

If a validator double-signs (produces two different blocks at the same height), any node can submit an EVIDENCE transaction containing both signatures as proof.

When slashing occurs:
- All stakes and delegations to that validator are reduced by **50%**
- The validator is removed from the active set if their remaining stake falls below the minimum (1)
- The validator is permanently barred from receiving new stake
- The slashing event is recorded and viewable at `GET /api/v1/slashing-events`

To protect yourself as a delegator: choose validators with a track record of uptime and correct operation. Never delegate to the same validator with all your stake.

## View Stake Information

```bash
# All validators with their stakes
curl http://localhost:8545/api/v1/stakes

# Specific validator
curl http://localhost:8545/api/v1/stakes/qv1VALIDATOR_ADDRESS

# Slashing history
curl http://localhost:8545/api/v1/slashing-events
```

## Next Steps

- [Explore the web dashboard's staking panel](05-dashboard.md)
- [Understand the fee system](08-fees.md)
- [Run a multi-node testnet](09-testnet.md)
