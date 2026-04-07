# Client-Side ML-DSA-65 Signing for QBit SDK

> **Status:** Draft  
> **Author:** API Architect (TechBi)  
> **Date:** 2026-04-02  
> **Parent Issue:** TEC-1225

## 1. Problem Statement

The current Python SDK (`sdk/qbit_sdk/client.py`) requires wallet keys to reside on the node. Every transaction-creating method (e.g., `transfer()`, `notarize()`) sends a `wallet_address` to the node, which then looks up the secret key, signs, and broadcasts.

**This is a fundamental security limitation:**
- Private keys traverse the network (even over TLS, the node operator has access).
- No support for hardware security modules (HSM), air-gapped signing, or browser wallets.
- Violates the principle of least privilege — the node does not need signing authority.

## 2. Current Architecture

```
┌─────────┐   wallet_address    ┌──────────┐   sign + broadcast
│  Client  │ ─────────────────► │   Node   │ ──────────────────►  Network
│  (SDK)   │   POST /transfer   │  (keys)  │
└─────────┘                     └──────────┘
```

### Existing Raw TX Endpoint (already implemented)

The node **already supports** pre-signed transaction submission:

| Transport   | Method / Path                    | Status       |
|-------------|----------------------------------|--------------|
| JSON-RPC    | `qv_sendRawTransaction`          | Implemented  |
| REST        | `POST /api/v1/txs`              | Implemented  |

However, the SDK does not expose client-side key generation or signing, and the REST endpoint is **not documented** in `docs/openapi.yaml`.

## 3. Target Architecture

```
┌─────────────────────────┐          ┌──────────────────┐
│         Client          │          │       Node       │
│                         │          │                  │
│  1. keygen() → sk, pk   │          │                  │
│  2. build_tx()          │          │                  │
│  3. sign(sk, tx)        │          │                  │
│  4. submit_signed(tx) ──┼────────► │  5. verify(tx)   │
│                         │  POST    │  6. broadcast    │
│  Keys never leave       │ /api/v1  │                  │
│  the client             │  /txs    │                  │
└─────────────────────────┘          └──────────────────┘
```

## 4. Transaction Signing Specification

### 4.1 Canonical Signable Bytes

The bytes-to-sign are a deterministic JSON serialization of the transaction fields:

```json
{"chainId":"qbit-mainnet","from":"qv1...","maxFeePerWeight":0,"maxPriorityFee":0,"nonce":0,"payload":{...},"timestamp":1700000000,"to":"qv1...","type":"TRANSFER"}
```

**Rules:**
- Keys sorted alphabetically (`sort_keys=True`)
- No whitespace (`separators=(',', ':')`)
- UTF-8 encoded

**Python reference:**
```python
import json

signable_json = json.dumps({
    "chainId": tx.chain_id,
    "from": tx.sender,
    "maxFeePerWeight": tx.max_fee_per_weight,
    "maxPriorityFee": tx.max_priority_fee,
    "nonce": tx.nonce,
    "payload": tx.payload,
    "timestamp": tx.timestamp,
    "to": tx.recipient,
    "type": tx.tx_type.value,
}, sort_keys=True, separators=(',', ':'))

signable_bytes = signable_json.encode('utf-8')
```

### 4.2 Transaction ID

```
tx_id = hex(SHA3-256(signable_bytes))
```

The TX ID is computed from the same signable bytes, before signing.

### 4.3 ML-DSA-65 Signature

| Parameter       | Value                        |
|-----------------|------------------------------|
| Algorithm       | ML-DSA-65 (FIPS 204)        |
| Secret key size | 4032 bytes                   |
| Public key size | 1952 bytes                   |
| Signature size  | 3309 bytes                   |
| Library         | liboqs 0.15.0 (`oqs` Python) |

```python
from oqs import Signature

signer = Signature("ML-DSA-65", secret_key=sk_bytes)
signature = signer.sign(signable_bytes)
```

### 4.4 Address Derivation

```
address = "qv1" + hex(SHA3-256(signing_public_key))
```

- Input: 1952-byte ML-DSA-65 public key
- Output: 67-character string (3-char prefix + 64 hex digits)

## 5. API Contract: `POST /api/v1/txs`

### 5.1 Request

```http
POST /api/v1/txs HTTP/1.1
Host: node.example.com:8545
Content-Type: application/json
Authorization: Bearer <token>
```

```json
{
  "type": "TRANSFER",
  "from": "qv1a1b2c3d4e5f...",
  "to": "qv1f6e5d4c3b2a...",
  "timestamp": 1700000000,
  "nonce": 0,
  "chainId": "qbit-mainnet",
  "maxFeePerWeight": 0,
  "maxPriorityFee": 0,
  "payload": {
    "amount": 1000000,
    "memo": "Payment for services"
  },
  "signature": "3a4b5c6d...",
  "sender_pubkey": "7e8f9a0b..."
}
```

**Field Requirements:**

| Field             | Type   | Required | Description                                       |
|-------------------|--------|----------|---------------------------------------------------|
| `type`            | string | Yes      | TX type enum (see Section 5.3)                    |
| `from`            | string | Yes      | Sender address (`qv1` + 64 hex)                   |
| `to`              | string | Depends  | Recipient address (required for TRANSFER, SHARE, MINT_TOKEN, TRANSFER_TOKEN) |
| `timestamp`       | int    | Yes      | Unix timestamp (seconds)                           |
| `nonce`           | int    | Yes      | Sender's next sequence number                      |
| `chainId`         | string | Yes      | Must match node's chain ID (`qbit-mainnet`)       |
| `maxFeePerWeight` | int    | Yes      | EIP-1559 base fee cap (0 if fees disabled)         |
| `maxPriorityFee`  | int    | Yes      | EIP-1559 priority tip (0 if fees disabled)         |
| `payload`         | object | Yes      | Type-specific payload (see Section 5.3)            |
| `signature`       | string | Yes      | ML-DSA-65 signature over signable bytes (hex)      |
| `sender_pubkey`   | string | Yes      | ML-DSA-65 public key (hex, 3904 hex chars = 1952 bytes) |

### 5.2 Response

**Success (201 Created):**
```json
{
  "data": { "tx_id": "a1b2c3d4e5f6..." },
  "error": null
}
```

**Validation Error (400):**
```json
{
  "data": null,
  "error": { "code": 400, "message": "invalid signature" }
}
```

**Common error messages:**
- `"invalid signature"` — ML-DSA-65 verification failed
- `"sender_pubkey does not match from address"` — address derivation mismatch
- `"nonce too low"` — nonce already used
- `"insufficient balance"` — for TRANSFER, STAKE, etc.
- `"invalid payload"` — payload validation failed for the TX type

### 5.3 Transaction Types and Payloads

| Type               | Payload Fields                                                         | `to` Required |
|--------------------|------------------------------------------------------------------------|---------------|
| `REGISTER_KEY`     | `encryption_pk` (hex)                                                  | No            |
| `REGISTER_VALIDATOR` | `validator_pubkey` (hex), `validator_address`, `commission` (0-100)  | No            |
| `REVOKE_KEY`       | `key_type`, `reason`                                                   | No            |
| `NOTARIZE`         | `documentHash` (hex), `metadata`? (string, max 1024)                  | No            |
| `STORE`            | `documentHash` (hex), `cid` (string), `metadata`?                     | No            |
| `SHARE`            | `cid`, `encapsulatedKey` (hex), `expires`? (int)                      | Yes           |
| `STAKE`            | `amount` (int 1–1M), `validator_address`                               | No            |
| `DELEGATE`         | `amount` (int 1–1M), `validator_address`                               | No            |
| `UNSTAKE`          | `amount` (int 1–1M), `validator_address`                               | No            |
| `EVIDENCE`         | `evidence_type`, `validator_address`, `block_index`, `block_a_hash`, `block_b_hash`, `block_a_sig`, `block_b_sig`, `block_a_header`, `block_b_header` | No |
| `TRANSFER`         | `amount` (int > 0), `memo`? (string, max 256)                         | Yes           |
| `ISSUE_TOKEN`      | `name`, `symbol`, `decimals` (0-18), `max_supply`, `transferable`?     | No            |
| `MINT_TOKEN`       | `token_id` (hex), `amount` (int > 0)                                   | Yes           |
| `TRANSFER_TOKEN`   | `token_id` (hex), `amount` (int > 0), `memo`?                          | Yes           |

### 5.4 Payload Size Limits

- Standard transactions: **8 KB** max
- EVIDENCE transactions: **32 KB** max

## 6. SDK Enhancement Plan

### 6.1 New Module: `qbit_sdk.signer`

```python
from qbit_sdk.signer import KeyPair, TransactionBuilder

# Generate keys (offline — no node connection needed)
kp = KeyPair.generate()
print(kp.address)      # "qv1..."
print(kp.public_key)   # bytes (1952)
kp.save("wallet.enc", password="strongpassword")

# Load existing keys
kp = KeyPair.load("wallet.enc", password="strongpassword")

# Build and sign a transaction
tx = TransactionBuilder(chain_id="qbit-mainnet") \
    .transfer(to="qv1...", amount=1000, memo="test") \
    .set_sender(kp.address) \
    .set_nonce(5) \
    .build()

signed_tx = kp.sign(tx)

# Submit to node
from qbit_sdk.client import QBitClient
client = QBitClient("http://localhost:8545")
result = client.submit_signed(signed_tx)
print(result["tx_id"])
```

### 6.2 New SDK Classes

#### `KeyPair`

```python
class KeyPair:
    address: str                 # qv1... (derived)
    public_key: bytes            # ML-DSA-65 PK (1952 bytes)
    _secret_key: SecureBytes     # ML-DSA-65 SK (4032 bytes, zeroed on GC)

    @classmethod
    def generate(cls) -> 'KeyPair':
        """Generate a new ML-DSA-65 keypair. No network call."""

    @classmethod
    def load(cls, path: str, password: str) -> 'KeyPair':
        """Load encrypted keypair from file.
        Format: AES-256-GCM with scrypt KDF (n=2^14, r=8, p=1)."""

    def save(self, path: str, password: str) -> None:
        """Save encrypted keypair. Sets file permissions to 0o600."""

    def sign(self, tx: UnsignedTransaction) -> SignedTransaction:
        """Sign a transaction. Returns new object; does not mutate input."""

    @classmethod
    def from_secret_key(cls, sk: bytes, pk: bytes) -> 'KeyPair':
        """Reconstruct keypair from raw secret key and public key bytes.
        PK cannot be derived from SK for ML-DSA-65."""
```

#### `TransactionBuilder`

```python
class TransactionBuilder:
    def __init__(self, chain_id: str = "qbit-mainnet"): ...

    # Factory methods for each TX type
    def transfer(self, to: str, amount: int, memo: str = "") -> 'TransactionBuilder': ...
    def notarize(self, document_hash: str, metadata: str = "") -> 'TransactionBuilder': ...
    def store(self, document_hash: str, cid: str, metadata: str = "") -> 'TransactionBuilder': ...
    def share(self, to: str, cid: str, encapsulated_key: str, expires: int = 0) -> 'TransactionBuilder': ...
    def stake(self, validator: str, amount: int) -> 'TransactionBuilder': ...
    def delegate(self, validator: str, amount: int) -> 'TransactionBuilder': ...
    def unstake(self, validator: str, amount: int) -> 'TransactionBuilder': ...
    def register_key(self, encryption_pk: str) -> 'TransactionBuilder': ...
    def register_validator(self, validator_pubkey: str, validator_address: str, commission: int = 0) -> 'TransactionBuilder': ...
    def revoke_key(self, key_type: str, reason: str) -> 'TransactionBuilder': ...
    def issue_token(self, name: str, symbol: str, decimals: int, max_supply: int = 0, transferable: bool = True) -> 'TransactionBuilder': ...
    def mint_token(self, to: str, token_id: str, amount: int) -> 'TransactionBuilder': ...
    def transfer_token(self, to: str, token_id: str, amount: int, memo: str = "") -> 'TransactionBuilder': ...

    # Common setters
    def set_sender(self, address: str) -> 'TransactionBuilder': ...
    def set_nonce(self, nonce: int) -> 'TransactionBuilder': ...
    def set_fee(self, max_fee_per_weight: int, max_priority_fee: int = 0) -> 'TransactionBuilder': ...
    def set_timestamp(self, ts: int) -> 'TransactionBuilder': ...

    def build(self) -> UnsignedTransaction:
        """Validate and return an UnsignedTransaction. Raises ValueError on invalid state."""
```

#### `QBitClient` additions

```python
class QBitClient:
    # Existing methods remain unchanged (backward compatible)

    # New methods for client-side signing workflow
    def submit_signed(self, tx: SignedTransaction) -> dict:
        """Submit a pre-signed transaction via POST /api/v1/txs.
        Returns {"tx_id": "..."}."""

    def get_nonce(self, address: str) -> int:
        """Fetch the next expected nonce for an address.
        Uses GET /api/v1/accounts/{address}."""
```

### 6.3 liboqs Packaging

The SDK will declare `liboqs-python` as an optional dependency:

```toml
[project.optional-dependencies]
pqc = ["liboqs-python>=0.15.0"]
```

Users who only need to submit pre-built signed TXs (e.g., from HSM) can skip `liboqs`. Users who want client-side keygen/signing install with `pip install qbit-sdk[pqc]`.

## 7. Key Storage Recommendations

| Method            | Security Level | Use Case                          |
|-------------------|----------------|-----------------------------------|
| Encrypted file    | Medium         | Development, personal wallets     |
| OS keychain       | High           | Desktop applications              |
| HSM (PKCS#11)     | Very High      | Enterprise, validators            |
| Air-gapped signer | Maximum        | Cold storage, treasury            |

### 7.1 Encrypted File Format (Default)

Compatible with existing node wallet format:

```json
{
  "version": 1,
  "address": "qv1...",
  "crypto": {
    "cipher": "aes-256-gcm",
    "kdf": "scrypt",
    "kdfparams": {
      "n": 16384,
      "r": 8,
      "p": 1,
      "dklen": 32,
      "salt": "<hex>"
    },
    "ciphertext": "<hex>",
    "nonce": "<hex>",
    "tag": "<hex>"
  }
}
```

**File permissions:** `0o600` (owner read/write only).

### 7.2 HSM Integration Path

For HSM integration, the `KeyPair` interface can be extended with a `SignerProtocol`:

```python
class SignerProtocol(Protocol):
    @property
    def address(self) -> str: ...
    @property
    def public_key(self) -> bytes: ...
    def sign(self, tx: UnsignedTransaction) -> SignedTransaction: ...
```

HSM adapters implement this protocol, allowing `submit_signed()` to work with any signer backend.

## 8. Migration Path

### Phase 1: Document & SDK (v0.9.0)
1. Document `POST /api/v1/txs` in OpenAPI spec (already implemented in node).
2. Add `KeyPair`, `TransactionBuilder`, `submit_signed()` to SDK.
3. Existing wallet-based methods continue to work unchanged.

### Phase 2: Deprecation Notices (v0.10.0)
1. Mark server-side signing SDK methods with deprecation warnings.
2. Add migration guide in SDK docs.
3. Node wallet creation endpoint gets deprecation header.

### Phase 3: Server-Side Signing Removal (v1.0.0)
1. Remove wallet storage from node (wallets become client-only).
2. Remove `POST /api/v1/transfer`, `POST /api/v1/notarize`, etc. (server-signed variants).
3. `POST /api/v1/txs` becomes the sole transaction submission endpoint.
4. JSON-RPC `qv_sendRawTransaction` remains as the RPC equivalent.

### Backward Compatibility

During Phase 1-2, both flows work simultaneously:

```
Client-side signing:  KeyPair.sign(tx) → client.submit_signed(tx)  [NEW]
Server-side signing:  client.transfer(wallet_addr, ...)             [DEPRECATED]
```

No breaking changes until v1.0.0.

## 9. Security Considerations

1. **Secret key never leaves client** — the `KeyPair._secret_key` field uses `SecureBytes` which zeros memory on garbage collection.
2. **Deterministic serialization** — canonical JSON prevents signature malleability.
3. **Address-pubkey binding** — node verifies `SHA3-256(sender_pubkey) == from` on every TX.
4. **Replay protection** — `chainId` + `nonce` prevent cross-chain and same-chain replay.
5. **Timestamp validation** — node rejects TXs with timestamps too far in the future.
6. **No key extraction API** — the node will never expose secret keys via RPC/REST.

## 10. OpenAPI Addition

See the companion `docs/openapi_client_signing.yaml` fragment or the updated `docs/openapi.yaml` for the full `POST /api/v1/txs` specification.
