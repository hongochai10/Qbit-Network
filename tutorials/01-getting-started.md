# Getting Started with QBit Network

## What is QBit Network?

QBit Network is a blockchain designed for two purposes: proving that a document existed at a specific time (notarization), and sharing files securely between users. Every transaction is signed with post-quantum cryptography, meaning its security holds even against an adversary with a large quantum computer.

Unlike general-purpose blockchains, QBit is purpose-built. There are no smart contracts. Instead, the chain has 11 built-in transaction types covering document operations, token transfers, and validator management. This makes the attack surface smaller and the protocol easier to audit — which is why the project has completed 17 security audit rounds with 0 open issues.

## What is Post-Quantum Cryptography?

Classical blockchains like Ethereum use ECDSA for signatures and ECDH for key exchange. Both algorithms rely on the difficulty of solving elliptic curve discrete logarithm problems. A sufficiently large quantum computer running Shor's algorithm can solve these problems efficiently, which would allow an attacker to forge signatures and impersonate any wallet.

Post-quantum cryptography (PQC) uses mathematical problems that remain hard even for quantum computers. QBit Network uses ML-DSA-65 (based on lattice problems) for all signatures and ML-KEM-768 for key encapsulation. Both are NIST standards (FIPS 204 and FIPS 203 respectively), finalized in 2024. Documents notarized today on QBit remain verifiable and unforgeable decades from now.

## Prerequisites

You need:

- Python 3.11 or newer
- The `liboqs` C library (provides ML-DSA and ML-KEM implementations)
- The Python packages: `liboqs-python`, `cryptography`, `aiohttp`

### Install liboqs (macOS)

```bash
cd /tmp
git clone --depth 1 --branch 0.15.0 https://github.com/open-quantum-safe/liboqs.git
cd liboqs && mkdir build && cd build
cmake -DBUILD_SHARED_LIBS=ON -DCMAKE_INSTALL_PREFIX=$HOME/_oqs ..
make -j$(sysctl -n hw.ncpu)
make install
```

### Install liboqs (Linux / Debian-based)

```bash
sudo apt-get install -y cmake ninja-build libssl-dev
cd /tmp
git clone --depth 1 --branch 0.15.0 https://github.com/open-quantum-safe/liboqs.git
cd liboqs && mkdir build && cd build
cmake -GNinja -DBUILD_SHARED_LIBS=ON -DCMAKE_INSTALL_PREFIX=/usr/local ..
ninja && sudo ninja install
sudo ldconfig
```

### Install Python packages

```bash
pip3 install liboqs-python cryptography aiohttp
```

### Verify the installation

```bash
python3 -c "import oqs; print('liboqs OK')"
```

You should see `liboqs OK`. If you get an import error, check that the shared library is on your library path:

```bash
# macOS
export DYLD_LIBRARY_PATH=$HOME/_oqs/lib:$DYLD_LIBRARY_PATH

# Linux
export LD_LIBRARY_PATH=/usr/local/lib:$LD_LIBRARY_PATH
```

### Clone the repository

```bash
git clone https://github.com/hongochai10/Qbit-Network.git
cd Qbit-Network
```

## Start a Node

The simplest way to run a node is:

```bash
python3 run_node.py
```

This does four things automatically:

1. Generates a new validator wallet (ML-DSA-65 + ML-KEM-768 keypair)
2. Creates a genesis block with an initial balance of 2,100,000 QBIT
3. Starts a P2P listener on port `9000`
4. Starts the REST API, JSON-RPC, and WebSocket server on port `8545`

The RPC authentication token is printed at startup. Save it — you need it for write operations.

### Custom data directory and token

For a persistent node that survives restarts:

```bash
python3 run_node.py --data-dir ./mynode --rpc-port 8545 --p2p-port 9000
```

Data is stored in `./mynode/`:
- `chain.db` — SQLite database with all blocks and transactions
- `wallets/` — encrypted wallet files

### Observer node (no block production)

```bash
python3 run_node.py --no-validate --peers 10.0.0.2:9000
```

An observer node syncs the chain and serves the API, but does not produce blocks.

### Connect to peers

```bash
python3 run_node.py --peers 10.0.0.2:9000 10.0.0.3:9000
```

Peers are comma or space separated. For local network testing, set the environment variable:

```bash
QBIT_ALLOW_PRIVATE_PEERS=1 python3 run_node.py --peers 192.168.1.10:9000
```

## Verify the Node is Running

Check the health endpoint:

```bash
curl http://localhost:8545/api/v1/health
```

Expected response:

```json
{"data": {"status": "ok"}, "error": null}
```

Get node information:

```bash
curl http://localhost:8545/api/v1/info
```

Expected response (abbreviated):

```json
{
  "data": {
    "version": "0.6.0",
    "chain_id": "qbit-mainnet",
    "height": 1,
    "peers": 0,
    "wallets": 1
  },
  "error": null
}
```

Get the latest block:

```bash
curl http://localhost:8545/api/v1/blocks/latest
```

## Open the Web Dashboard

The built-in single-page dashboard is available without any extra setup:

```
http://localhost:8545/dashboard/
```

It shows live chain statistics, recent blocks, pending transactions, and validator information. The dashboard connects to the node via WebSocket for real-time updates.

For the full NextJS dashboard with additional pages (Transfer, Notarize, Wallets, Validators):

```bash
cd web
npm install
npm run dev
# Dashboard available at http://localhost:3000
```

The NextJS dashboard needs to know where your node is running. Go to the Settings page (gear icon) and enter `http://localhost:8545` as the API URL along with your RPC token.

## What Just Happened?

When the node started, it:

1. Generated a validator wallet using ML-DSA-65. The wallet address is `qv1` followed by 64 hex characters derived from the signing public key.
2. Created a genesis block (index 0) containing a `REGISTER_VALIDATOR` transaction and a balance allocation of 2,100,000 QBIT to the validator address.
3. Began producing new blocks every 5 seconds (when there are pending transactions). Empty blocks are not produced.
4. Started listening for P2P connections from other nodes, which use a 4-step ML-DSA mutual authentication handshake before exchanging any chain data.

## Next Steps

- [Create a wallet and send QBIT](02-wallets-and-transfers.md)
- [Notarize your first document](03-notarization.md)
- [Understand the fee system](08-fees.md)
- [Run a multi-node testnet](09-testnet.md)
