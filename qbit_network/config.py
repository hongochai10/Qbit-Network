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

# Protocol
PROTOCOL_VERSION = 3

# Network
DEFAULT_P2P_PORT = 9000
DEFAULT_RPC_PORT = 8545
MAX_PEERS = 50
MAX_RPC_BODY = 1 * 1024 * 1024   # 1 MB max RPC request body
MAX_RPC_BATCH = 50

# Security
ALLOW_PRIVATE_PEERS = os.environ.get("QBIT_ALLOW_PRIVATE_PEERS", "").lower() in ("1", "true", "yes")

# Rate limiting — P2P (per peer IP)
P2P_RATE_LIMIT = 20       # messages/second sustained
P2P_RATE_BURST = 100      # max burst capacity
P2P_RATE_VIOLATIONS_MAX = 3  # disconnects after this many violations

# Rate limiting — RPC (per client IP)
RPC_RATE_LIMIT = 10       # requests/second sustained
RPC_RATE_BURST = 50       # max burst capacity

# Staking / dPoS
MIN_STAKE = 1
MAX_STAKE = 1_000_000
UNBONDING_PERIOD = 100       # blocks until unstaked weight is released
EPOCH_LENGTH = 100           # blocks per epoch
SLASH_PERCENTAGE = 50        # percentage of total stake slashed for misbehaviour
DEFAULT_COMMISSION_RATE = 10 # percent of epoch rewards kept by validator

# TLS
TLS_CERT_VALIDITY_DAYS = 365
TLS_RENEWAL_THRESHOLD_DAYS = 30

# Token economics
TOKEN_NAME = "QBit"
TOKEN_SYMBOL = "QBIT"
TOKEN_DECIMALS = 9
QUBIT_PER_QBIT = 10 ** TOKEN_DECIMALS  # 1_000_000_000
MAX_SUPPLY = 21_000_000 * QUBIT_PER_QBIT  # 2.1 * 10^16

# Block rewards
INITIAL_BLOCK_REWARD = 5 * QUBIT_PER_QBIT  # 5_000_000_000 qubits
HALVING_INTERVAL = 2_100_000

# Fees (in qubits — smallest unit, 1 QBIT = 10^9 qubits)
TX_FEES = {
    "TRANSFER": 1_000_000,        # 0.001 QBIT
    "NOTARIZE": 10_000_000,       # 0.01 QBIT
    "STORE": 20_000_000,          # 0.02 QBIT
    "SHARE": 10_000_000,          # 0.01 QBIT
    "REGISTER_KEY": 100_000_000,  # 0.1 QBIT
    "REGISTER_VALIDATOR": 1_000_000_000,  # 1.0 QBIT
    "STAKE": 10_000_000,          # 0.01 QBIT
    "DELEGATE": 10_000_000,       # 0.01 QBIT
    "UNSTAKE": 10_000_000,        # 0.01 QBIT
    "REVOKE_KEY": 0,
    "EVIDENCE": 0,
}
FEE_BURN_PERCENT = 50

# EIP-1559 Dynamic Fee
TX_WEIGHTS = {
    "TRANSFER": 100_000,
    "NOTARIZE": 1_000_000,
    "STORE": 2_000_000,
    "SHARE": 1_000_000,
    "REGISTER_KEY": 10_000_000,
    "REGISTER_VALIDATOR": 100_000_000,
    "STAKE": 1_000_000,
    "DELEGATE": 1_000_000,
    "UNSTAKE": 1_000_000,
    "REVOKE_KEY": 0,
    "EVIDENCE": 0,
}
MAX_BLOCK_WEIGHT = 20_000_000
TARGET_BLOCK_WEIGHT = MAX_BLOCK_WEIGHT // 2
BASE_FEE_CHANGE_DENOM = 8
INITIAL_BASE_FEE = 10
MIN_BASE_FEE = 1
MAX_BASE_FEE = 10_000
DYNAMIC_FEE_ACTIVATION_HEIGHT = 2**63  # set to 0 for new chains; high default preserves legacy behavior
MAX_SELF_TX_WEIGHT_RATIO = 25  # percent

# Financial activation
FINANCIAL_ACTIVATION_HEIGHT = 0  # Active from genesis for new chains

# Genesis allocation (in QBIT tokens, will be multiplied by QUBIT_PER_QBIT)
GENESIS_BALANCE_QBIT = 2_100_000  # 2.1M QBIT (10% of max supply)

# Version
VERSION = "0.7.0"

# Pruning
PRUNING_RETENTION = 10000     # blocks to keep during chain pruning

# Storage
DATA_DIR = os.environ.get("QBIT_DATA_DIR", os.path.expanduser("~/.qbit"))
