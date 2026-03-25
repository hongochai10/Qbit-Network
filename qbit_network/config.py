"""QBit Network blockchain configuration."""
import os

# Crypto
MLDSA_ALGORITHM = "ML-DSA-65"
MLKEM_ALGORITHM = "ML-KEM-768"

# Chain
BLOCK_INTERVAL = 5          # seconds between blocks
MAX_TX_PER_BLOCK = 200
MAX_BLOCK_SIZE = 5 * 1024 * 1024  # 5 MB max serialized block size
MAX_TX_PAYLOAD_SIZE = 8192        # 8 KB max payload per tx
MAX_TX_POOL_SIZE = 10000
MAX_BLOCK_DRIFT = 30              # seconds into the future allowed
ADDRESS_PREFIX = "qv1"
CHAIN_ID = "qbit-mainnet"
MAX_REORG_DEPTH = 32              # max blocks to reorganize

# Network
DEFAULT_P2P_PORT = 9000
DEFAULT_RPC_PORT = 8545
MAX_PEERS = 50
MAX_RPC_BODY = 1 * 1024 * 1024   # 1 MB max RPC request body
MAX_RPC_BATCH = 50

# Security
ALLOW_PRIVATE_PEERS = os.environ.get("QBIT_ALLOW_PRIVATE_PEERS", "").lower() in ("1", "true", "yes")

# Storage
DATA_DIR = os.environ.get("QBIT_DATA_DIR", os.path.expanduser("~/.qbit"))
