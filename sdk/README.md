# QBit Network Python SDK

Python SDK for the QBit Network post-quantum blockchain. Zero external dependencies -- uses only the Python standard library.

## Installation

```bash
pip install qbit-sdk
```

Or install from source:

```bash
cd sdk/
pip install -e .
```

## Quick Start

```python
from qbit_sdk import QBitClient

# Connect to a local node
client = QBitClient("http://localhost:8545/api/v1", token="your-auth-token")

# Check node status
info = client.get_info()
print(f"Chain height: {info.chain_height}")

# Create a wallet
wallet = client.create_wallet()
print(f"Address: {wallet.address}")

# Notarize a document
tx_id = client.notarize(wallet.address, "sha256hash_of_document")
print(f"TX: {tx_id}")

# Get receipt
receipt = client.get_receipt(tx_id)
print(f"Status: {receipt.status}, Events: {len(receipt.events)}")

# Verify a document
result = client.verify_document("sha256hash_of_document")
print(f"Verified: {result.verified}")

# Get state proof
proof = client.get_state_proof(wallet.address, "balance")
print(f"State root: {proof.state_root}")
```

## WebSocket Subscriptions

```python
from qbit_sdk.websocket import QBitWebSocket

ws = QBitWebSocket("ws://localhost:8545/ws")
ws.connect()
ws.subscribe("new_block", lambda data: print(f"New block: {data['index']}"))
ws.run_in_background()
```

## Webhooks

```python
# Register a webhook
webhook = client.register_webhook(
    url="https://example.com/webhook",
    events=["Transfer", "Notarize"],
    secret="my-hmac-secret",
)

# List webhooks
for wh in client.list_webhooks():
    print(f"{wh.id}: {wh.url} ({wh.status})")

# Delete a webhook
client.delete_webhook(webhook.id)
```

## API Reference

See the [OpenAPI spec](../docs/openapi.yaml) for the complete API reference.
