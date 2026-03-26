# Running a Testnet

## Single Node (Quick Start)

The fastest way to run a testnet is a single node with a fresh data directory:

```bash
python3 run_node.py --data-dir ./testnet-data --rpc-port 8545 --p2p-port 9000
```

The node prints its RPC auth token at startup. To set a fixed token instead (useful for scripting):

```bash
QBIT_RPC_TOKEN=mytoken python3 run_node.py --data-dir ./testnet-data
```

Note: `QBIT_RPC_TOKEN` is not a recognized environment variable in the current implementation — the token is generated at startup. To retrieve it, read the startup log or use a fixed token by modifying the node configuration.

Check it is running:

```bash
curl http://localhost:8545/api/v1/health
curl http://localhost:8545/api/v1/info
```

## Multi-Node Testnet with docker-compose

The repository includes a `docker-compose.yml` that starts a 3-validator testnet:

```bash
docker-compose up -d
```

This starts three nodes:
- Node 1: RPC at `http://localhost:8545`, P2P at port 9000
- Node 2: RPC at `http://localhost:8546`, P2P at port 9001
- Node 3: RPC at `http://localhost:8547`, P2P at port 9002

The nodes connect to each other automatically via the Docker network. You can query any of them:

```bash
curl http://localhost:8545/api/v1/info
curl http://localhost:8546/api/v1/validators
curl http://localhost:8547/api/v1/blocks/latest
```

Stop and clean up:

```bash
docker-compose down -v
```

The `-v` flag removes the volumes (chain data). Omit it to preserve the chain across restarts.

## Manual Multi-Node Setup

To run multiple nodes on the same machine (for development and testing):

**Terminal 1 — Node A (genesis node):**

```bash
python3 run_node.py \
  --data-dir ./testnet/nodeA \
  --rpc-port 8545 \
  --p2p-port 9000
```

Note the validator address and RPC token printed at startup.

**Terminal 2 — Node B (connects to A):**

```bash
QBIT_ALLOW_PRIVATE_PEERS=1 python3 run_node.py \
  --data-dir ./testnet/nodeB \
  --rpc-port 8546 \
  --p2p-port 9001 \
  --peers 127.0.0.1:9000
```

`QBIT_ALLOW_PRIVATE_PEERS=1` is required because loopback addresses are blocked by default for security.

**Terminal 3 — Node C (connects to A):**

```bash
QBIT_ALLOW_PRIVATE_PEERS=1 python3 run_node.py \
  --data-dir ./testnet/nodeC \
  --rpc-port 8547 \
  --p2p-port 9002 \
  --peers 127.0.0.1:9000
```

Verify peers are connected:

```bash
curl http://localhost:8545/api/v1/info
# "peers": 2
```

## Registering Additional Validators

Node A starts as the only registered validator. To activate dPoS selection with multiple validators, register and stake Nodes B and C.

First, get Node B's validator address from its startup log. Then, using Node A's token (which has genesis balance):

```bash
# On Node B, create a wallet and note the address
curl -X POST http://localhost:8546/api/v1/wallets \
  -H "Authorization: Bearer NODE_B_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"password": ""}'
```

Fund Node B's address from Node A's genesis balance:

```bash
curl -X POST http://localhost:8545/api/v1/transfer \
  -H "Authorization: Bearer NODE_A_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "wallet": "qv1NODE_A_ADDRESS",
    "to": "qv1NODE_B_ADDRESS",
    "amount": 100000000000
  }'
```

Register Node B as a validator:

```bash
curl -X POST http://localhost:8546/api/v1/register-validator \
  -H "Authorization: Bearer NODE_B_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"wallet": "qv1NODE_B_ADDRESS"}'
```

Stake on Node B:

```bash
curl -X POST http://localhost:8546/api/v1/stake \
  -H "Authorization: Bearer NODE_B_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "wallet": "qv1NODE_B_ADDRESS",
    "validator_address": "qv1NODE_B_ADDRESS",
    "amount": 1000
  }'
```

Repeat for Node C. After the next epoch boundary (100 blocks), both new validators will be active in dPoS selection.

## Command Line Options

| Flag | Default | Description |
|------|---------|-------------|
| `--rpc-port` | 8545 | REST API and JSON-RPC port |
| `--p2p-port` | 9000 | P2P TCP listener port |
| `--data-dir` | `~/.qbit` | Data directory for chain.db and wallets |
| `--peers` | none | Initial peers to connect to (host:port) |
| `--wallet` | auto-generated | Path to an existing wallet file |
| `--no-validate` | false | Run as observer (no block production) |
| `--tls-auto` | false | Auto-generate and renew TLS certificate |
| `--tls-hostname` | localhost | Hostname for TLS certificate |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `QBIT_DATA_DIR` | `~/.qbit` | Override default data directory |
| `QBIT_ALLOW_PRIVATE_PEERS` | false | Allow connections to RFC 1918 addresses |

## Monitoring

**Watch logs** — the node prints block production and P2P events to stdout. Pipe to a log file for persistence:

```bash
python3 run_node.py --data-dir ./testnet-data > node.log 2>&1 &
tail -f node.log
```

**WebSocket monitoring** — subscribe to chain events for real-time monitoring:

```bash
# Using websocat (install separately)
websocat ws://localhost:8545/ws <<< '{"action":"subscribe","channel":"new_block"}'
```

Or use the built-in dashboard at `http://localhost:8545/dashboard/`.

**Poll the API** — check block height periodically:

```bash
while true; do
  curl -s http://localhost:8545/api/v1/info | python3 -m json.tool | grep height
  sleep 5
done
```

## TLS for Remote Nodes

For nodes accessible over the internet, enable TLS:

```bash
python3 run_node.py \
  --data-dir ./mainnet-data \
  --tls-auto \
  --tls-hostname mynode.example.com
```

This generates a self-signed certificate in the data directory, valid for 365 days, and renews it automatically when it has 30 days remaining. A SIGHUP signal triggers a manual reload.

For production, use a properly signed certificate:

```bash
python3 run_node.py \
  --data-dir ./mainnet-data \
  --tls-cert /etc/letsencrypt/live/mynode.example.com/fullchain.pem \
  --tls-key /etc/letsencrypt/live/mynode.example.com/privkey.pem
```

Hot-reload happens automatically when the certificate file modification time changes.

When TLS is enabled, the CLI and API clients need to use `https://` and `wss://`:

```bash
curl https://mynode.example.com:8545/api/v1/health
# For self-signed cert (testing only):
curl --insecure https://localhost:8545/api/v1/health
python3 cli/qbit.py notarize doc.pdf --rpc https://localhost:8545 --insecure
```

## Reset the Testnet

To start fresh, delete the data directory:

```bash
rm -rf ./testnet-data
python3 run_node.py --data-dir ./testnet-data
```

This creates a new genesis block with a new validator wallet. All previous chain state is discarded.

## Next Steps

- [Security model and what has been audited](10-security.md)
- [REST API reference](07-rest-api.md)
- [Fee system](08-fees.md)
