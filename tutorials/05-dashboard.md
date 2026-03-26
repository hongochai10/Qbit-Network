# Web Dashboard

QBit Network ships with two web interfaces:

- **Built-in SPA** at `http://localhost:8545/dashboard/` — no setup required, works immediately when the node is running
- **NextJS dashboard** at `http://localhost:3000` — full-featured with additional pages for transfers, notarization, and wallet management

This tutorial covers the NextJS dashboard. For a quick look at chain state without any setup, the built-in SPA is sufficient.

## Starting the NextJS Dashboard

From the repository root:

```bash
cd web
npm install
npm run dev
```

The dashboard starts at `http://localhost:3000`. On first load, it will show a connection error until you configure the node URL and token.

## Connecting to Your Node

1. Click the gear icon in the top-right corner to open Settings
2. Enter your node URL: `http://localhost:8545`
3. Enter your RPC auth token (printed at node startup)
4. Click Save

Settings are stored in your browser's local storage. The dashboard immediately retries the connection.

To verify the connection, the stats bar at the top of every page shows the current chain height, pending pool size, and number of connected peers. If these update every few seconds, the WebSocket connection is working.

## Dashboard Overview

The main dashboard page (`/`) shows:

**Stats Bar** — updates in real time via WebSocket every 5 seconds:
- Chain height (total blocks)
- Total transactions confirmed
- Pending pool size (transactions waiting to be mined)
- Active validator count
- Average block time

**Recent Blocks** — the 10 most recent blocks. Click any block to see its transactions. Each row shows the block index, truncated hash, timestamp, and the validator who produced it.

**Transaction Pool** — transactions currently waiting to be mined, grouped by type. This updates in real time as new transactions arrive.

**Supply Widget** — shows total minted QBIT, circulating supply, and staked QBIT.

## Block Explorer

Navigate to `/blocks` to browse the full block history. Blocks are listed newest-first with pagination (20 per page by default).

**Browsing blocks:**
- Click a block row to expand its transaction list
- Click the block index link to go to the block detail page
- Use the page controls at the bottom to navigate older blocks

**Block detail page** (`/blocks/42`):
- Block hash, previous hash, Merkle root
- Timestamp and block index
- Validator address (who produced the block)
- Full transaction list with type badges

## Transaction Viewer

Navigate to `/transactions` to search for a transaction by ID.

1. Paste the transaction ID (64 hex characters)
2. Click Search
3. The result shows the transaction type, sender, recipient (if applicable), timestamp, payload, and which block it was mined in

Transaction types are color-coded:
- TRANSFER — blue
- NOTARIZE — green
- STORE / SHARE — purple
- STAKE / DELEGATE / UNSTAKE — orange
- REGISTER_VALIDATOR / REGISTER_KEY — yellow
- REVOKE_KEY / EVIDENCE — red

## Wallets Page

Navigate to `/wallets` to manage wallets loaded on your node.

**Create a wallet:**
1. Click "Create Wallet"
2. Enter a password (or leave blank for an unencrypted test wallet)
3. The new address appears in the list immediately

**View wallet details:**
- Click any address to see its balance in QBIT
- The balance shown is confirmed balance (does not include pending pool debits)
- The encryption key registration status is shown — if unregistered, others cannot send you encrypted files

## Transfer Page

Navigate to `/transfer` to send QBIT between addresses.

1. Select the sender wallet from the dropdown (wallets loaded on the node)
2. Enter the recipient `qv1` address
3. Enter the amount in QBIT
4. Optionally add a memo (max 256 characters)
5. Optionally set a priority fee (in qubits per weight unit) to get faster inclusion during congestion
6. Click "Send QBIT"

The form validates the recipient address format before submission. After submission, the transaction ID is displayed and you can click it to open the transaction viewer.

The current base fee is shown on the page, so you know what `maxFeePerWeight` to set. A safe default is `base_fee + 10` for prompt inclusion.

## Notarize and Verify Page

Navigate to `/notarize` for document operations.

**Notarize a document:**
1. Select the wallet to sign with
2. Drag your file onto the upload area, or click to browse
3. The SHA3-256 hash is computed in your browser — the file is never uploaded to the node
4. Add optional metadata
5. Click "Notarize"

**Verify a document:**
1. Switch to the Verify tab
2. Drop the file or paste the hash directly
3. The result shows notarization details (block, timestamp, who notarized it), or "Not found" if the hash is not on-chain

## Validators Page

Navigate to `/validators` to see all registered validators and their stake.

The page shows:
- Validator address
- Total stake weight
- Number of delegators
- Commission rate
- Slashed status (slashed validators are highlighted)
- Current epoch info at the top

**Stake, delegate, and unstake:**
1. Click on a validator row to expand the stake panel
2. Choose Stake (if this is your own validator), Delegate, or Unstake
3. Enter the amount
4. Confirm the transaction

The epoch info section shows the current epoch number, how many blocks remain until the next epoch, and the list of validators active in this epoch.

## Supply Overview

The supply widget (visible on the main dashboard and as a standalone panel in the validators page) shows:

- **Total minted** — all QBIT ever created via block rewards
- **Circulating** — minted minus burned (pre-activation fees burned 50% of each fee)
- **Staked** — total QBIT currently locked in stake and delegation positions
- **Conservation check** — circulating + staked + burned should equal total minted

After the EIP-1559 dynamic fee activation, fees are no longer burned — 100% go to the block-producing validator.

## Real-Time Updates

The dashboard uses WebSocket (`ws://localhost:8545/ws`) for live updates. It subscribes to three channels:

- `new_block` — updates the block list and height counter when a block is produced
- `new_tx` — updates the pool display when a new transaction arrives
- `chain_stats` — updates all counters every 5 seconds

If the WebSocket disconnects (network issue or node restart), the dashboard automatically reconnects with exponential backoff. A small indicator in the stats bar shows the connection status.

## Next Steps

- [Learn the REST API](07-rest-api.md)
- [Understand how fees work](08-fees.md)
- [Run a multi-node testnet](09-testnet.md)
