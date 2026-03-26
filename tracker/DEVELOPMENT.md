# Development Guide

## Setup

### 1. Install liboqs (C library)

```bash
# macOS (build with shared library)
cd /tmp && git clone --depth 1 --branch 0.15.0 https://github.com/open-quantum-safe/liboqs.git
cd liboqs && mkdir build && cd build
cmake -DBUILD_SHARED_LIBS=ON -DCMAKE_INSTALL_PREFIX=$HOME/_oqs ..
make -j$(sysctl -n hw.ncpu) && make install

# The Python wrapper looks for liboqs in $HOME/_oqs/lib by default
```

### 2. Install Python dependencies

```bash
pip3 install liboqs-python cryptography aiohttp
```

### 3. Verify

```bash
python3 -c "
import oqs
sig = oqs.Signature('ML-DSA-65')
pk = sig.generate_keypair()
print(f'ML-DSA-65 OK: pk={len(pk)} bytes')

kem = oqs.KeyEncapsulation('ML-KEM-768')
pk2 = kem.generate_keypair()
print(f'ML-KEM-768 OK: pk={len(pk2)} bytes')
"
```

## Project Structure

```
qbit_network/
├── crypto/              # Pure cryptographic primitives (no business logic)
│   ├── mldsa.py         # ML-DSA-65 sign/verify
│   ├── mlkem.py         # ML-KEM-768 encapsulate/decapsulate
│   ├── hashing.py       # SHA3, SHAKE, Merkle tree
│   ├── aes.py           # AES-256-GCM
│   └── secure_bytes.py  # ctypes-backed mutable key material with zero()
├── core/                # Blockchain data structures and state
│   ├── wallet.py        # Identity management (SecureBytes-backed keys)
│   ├── transaction.py   # 11 TX types and validation
│   ├── block.py         # Block structure with Merkle proofs and baseFee
│   ├── blockchain.py    # Chain state machine (dPoS, epochs, slashing, QBIT balance ledger, pruning)
│   ├── consensus.py     # dPoS weighted selection + PoA round-robin fallback
│   ├── fees.py          # EIP-1559 fee engine (compute_base_fee, compute_tx_fee, tx_weight)
│   ├── store.py         # SQLite backend (blocks, validators, stakes, epochs, slashing, balances)
│   └── proof.py         # Merkle proof export + block signature verification
├── network/             # Communication layer
│   ├── p2p.py           # TCP P2P, HELLO_AUTH 4-step auth, ML-KEM encrypted channel
│   ├── rpc.py           # JSON-RPC 2.0 + WebSocket attach + dashboard static files
│   ├── rest_api.py      # REST API gateway (36 endpoints, /api/v1/)
│   ├── websocket.py     # WebSocket pub/sub (new_block, new_tx, chain_stats)
│   ├── rate_limiter.py  # Token bucket rate limiting (P2P + RPC)
│   ├── tls_manager.py   # TLS auto-provisioning, renewal, hot-reload
│   └── reputation.py    # PeerReputation scoring with decay and banning
├── node.py              # Orchestrator (ties everything together)
└── config.py            # All configuration constants
cli/
├── qbit.py              # CLI entry point (wallet, notarize, verify, store, share, retrieve, proof)
└── ipfs_client.py       # Lightweight IPFS HTTP API client (stdlib-only)
dashboard/
└── index.html           # Single-file SPA chain explorer (served at /dashboard/)
```

**Dependency direction**: `crypto` -> `core` -> `network` -> `node`

No circular imports. `crypto` has zero internal dependencies.

## Running Tests

```bash
# Quick smoke test
python3 -c "
from qvault.core.wallet import Wallet
from qvault.core.blockchain import Blockchain
from qvault.core.transaction import Transaction

w = Wallet.generate()
bc = Blockchain()
bc.consensus.add_validator(w.address, w.signing_pk)
bc.init_chain(w.address, w.signing_sk)

tx = Transaction.notarize(w.address, 'aabbccdd', nonce=0)
tx.sign(w.signing_sk, w.signing_pk)
ok, tid = bc.submit_tx(tx)
assert ok

block = bc.produce_block(w.address, w.signing_sk)
assert block is not None

result = bc.verify_document('aabbccdd')
assert result is not None
print('All OK')
"
```

## Code Conventions

- `__slots__` on Transaction and Block for memory efficiency + cache safety
- Cached properties: `tx_id`, `block_hash`, `_header_bytes`, `_signable_bytes`
- Factory classmethods: `Transaction.notarize()`, `Transaction.share()`, `Block.genesis()`
- Validation split: `from_dict()` does type checks, `validate_payload()` does business rules
- Consensus injected state: `_chain_nonces`, `_chain_tx_ids` shared by reference from Blockchain
- Per-address asyncio.Lock in node.py for atomic nonce+sign+submit

## Configuration

All limits are in `config.py`. Key parameters:

```python
BLOCK_INTERVAL = 5          # seconds between block production attempts
MAX_TX_PER_BLOCK = 200      # max transactions per block
MAX_TX_PAYLOAD_SIZE = 8192  # 8 KB max per-tx payload
MAX_TX_POOL_SIZE = 10000    # max pending transactions
MAX_BLOCK_DRIFT = 30        # max seconds a block timestamp can be in the future
CHAIN_ID = "qbit-mainnet"   # chain identifier for replay protection
EPOCH_LENGTH = 100          # blocks per dPoS epoch
UNBONDING_PERIOD = 100      # blocks before unstake takes effect
MIN_STAKE = 1               # minimum stake amount
MAX_STAKE = 1_000_000       # maximum stake amount
SLASH_PERCENTAGE = 50       # percentage of stake slashed on double-sign evidence
PRUNING_RETENTION = 10000   # blocks to retain when pruning
TLS_CERT_VALIDITY_DAYS = 365      # validity of auto-generated TLS certificates
TLS_RENEWAL_THRESHOLD_DAYS = 30   # days before expiry to trigger auto-renewal
```
