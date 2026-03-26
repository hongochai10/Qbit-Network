# Document Notarization

## What is Document Notarization?

Document notarization on a blockchain means recording a fingerprint (hash) of a document in a block. Because blocks are cryptographically chained and signed, the record is:

- **Tamper-proof** — altering the record requires re-computing the entire chain from that point
- **Timestamped** — the block's timestamp and index prove the document existed at that time
- **Verifiable by anyone** — the hash and block are public; no trusted third party is required
- **Quantum-resistant** — blocks are signed with ML-DSA-65, so proofs remain valid against quantum attackers

The document itself never touches the chain. Only its SHA3-256 hash is stored. This means you can notarize sensitive documents without exposing their contents.

## How It Works

1. You compute the SHA3-256 hash of your file (the CLI does this automatically)
2. You submit a NOTARIZE transaction containing that hash
3. The transaction is mined into a block
4. Anyone with the original file can later verify it was notarized by hashing the file and checking the chain

## Notarize a Document

### Via CLI (recommended)

```bash
python3 cli/qbit.py notarize contract.pdf --token YOUR_RPC_TOKEN
```

If you have multiple wallets, specify which one to use:

```bash
python3 cli/qbit.py notarize contract.pdf \
  --wallet qv1YOUR_ADDRESS \
  --token YOUR_RPC_TOKEN
```

Add optional metadata (description stored on-chain):

```bash
python3 cli/qbit.py notarize contract.pdf \
  --metadata "Service agreement v2, signed 2026-03-26" \
  --token YOUR_RPC_TOKEN
```

Expected output:

```
Notarized: contract.pdf
SHA3-256:  a1b2c3d4e5f6...
TX ID:     7f8e9d0a1b2c...
(wait for next block to confirm)
```

### Via REST API

First, compute the SHA3-256 hash of your file:

```bash
# Linux/macOS
sha3sum -a 256 contract.pdf
# or with Python
python3 -c "import hashlib; print(hashlib.sha3_256(open('contract.pdf','rb').read()).hexdigest())"
```

Then submit the notarization:

```bash
curl -X POST http://localhost:8545/api/v1/notarize \
  -H "Authorization: Bearer YOUR_RPC_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "wallet": "qv1YOUR_ADDRESS",
    "document_hash": "a1b2c3d4e5f6...",
    "metadata": "Service agreement v2"
  }'
```

Expected response:

```json
{
  "data": {
    "tx_id": "7f8e9d0a1b2c...",
    "document_hash": "a1b2c3d4e5f6..."
  },
  "error": null
}
```

### Via the Web Dashboard

1. Open the Notarize page in the NextJS dashboard
2. Either drag and drop your file or click to select it
3. The dashboard computes the SHA3-256 hash in your browser — the file is not uploaded anywhere
4. Click "Notarize" to submit the transaction
5. The transaction ID is shown once submitted

## Verify a Document

Verification confirms that a specific document hash was notarized on-chain. This is a public operation — no token required.

### Via CLI

```bash
python3 cli/qbit.py verify contract.pdf
```

Expected output (found):

```
VERIFIED: contract.pdf
  SHA3-256:   a1b2c3d4e5f6...
  Block:      #42
  Timestamp:  2026-03-26T14:22:10
  TX ID:      7f8e9d0a1b2c...
  Notarizer:  qv1SENDER_ADDRESS
```

Expected output (not found):

```
NOT FOUND: contract.pdf is not notarized on-chain
```

### Via REST API

```bash
curl -X POST http://localhost:8545/api/v1/verify \
  -H "Content-Type: application/json" \
  -d '{"document_hash": "a1b2c3d4e5f6..."}'
```

Expected response (found):

```json
{
  "data": {
    "document_hash": "a1b2c3d4e5f6...",
    "tx_id": "7f8e9d0a1b2c...",
    "block_index": 42,
    "timestamp": 1711458130,
    "sender": "qv1SENDER_ADDRESS"
  },
  "error": null
}
```

Expected response (not found):

```json
{
  "data": null,
  "error": {"code": 404, "message": "document hash not found"}
}
```

### Via the Web Dashboard

1. Go to the Notarize page
2. Click the "Verify" tab
3. Drop your file or enter the hash manually
4. The result shows the block number, timestamp, and who notarized it

## Export a Proof

A proof file contains everything needed to verify notarization without connecting to any node. It bundles the block header, a Merkle proof, and the document hash into a self-contained JSON file.

### Export as JSON

```bash
python3 cli/qbit.py proof contract.pdf
```

Creates `contract.pdf.proof.json`. To save to a custom location:

```bash
python3 cli/qbit.py proof contract.pdf --output /path/to/proof.json
```

### Export as HTML Certificate

```bash
python3 cli/qbit.py proof contract.pdf --format html
```

Creates `contract.pdf.proof.html` — an HTML page you can open in any browser to view and share the notarization certificate.

### Verify a Proof Offline

Proof files can be verified without a running node. The verification checks the Merkle proof and block header hash:

```bash
python3 cli/qbit.py verify-proof contract.pdf.proof.json
```

Expected output:

```
PROOF VALID
  Document:   a1b2c3d4e5f6...
  Block:      #42
  Timestamp:  2026-03-26T14:22:10
  Validator:  qv1VALIDATOR_ADDRESS
  Notarizer:  qv1SENDER_ADDRESS
```

This offline verification proves that:
1. The document hash was included in block #42 (Merkle proof)
2. The block header hash matches the recorded block hash

## Store a Document Reference with IPFS

The NOTARIZE transaction only stores a hash. To also make the document retrievable, use the STORE transaction, which records both the hash and an IPFS content identifier (CID).

```bash
python3 cli/qbit.py store document.pdf --ipfs --token YOUR_RPC_TOKEN
```

This pins the file to your local IPFS node and records the CID on-chain. Requires a running IPFS daemon (`ipfs daemon`).

Expected output:

```
Stored: document.pdf
  SHA3-256: a1b2c3d4e5f6...
  CID:      QmXyz123...
  TX ID:    9e8d7c6b...
  File pinned to IPFS.
```

To retrieve the file later:

```bash
python3 cli/qbit.py retrieve QmXyz123... --output ./retrieved-document.pdf
```

## Share a Document Encrypted

To share a file with a specific recipient so only they can decrypt it, use the SHARE command. The file is encrypted with ML-KEM-768 key encapsulation — the recipient's public encryption key is used to encapsulate an AES-256-GCM key, and only the recipient can decapsulate it.

The recipient must have previously registered their encryption key on-chain (via `wallet create --register`).

```bash
python3 cli/qbit.py share secret.pdf \
  --to qv1RECIPIENT_ADDRESS \
  --ipfs \
  --token YOUR_RPC_TOKEN
```

Expected output:

```
Shared: secret.pdf
  To:       qv1RECIPIENT_ADDRESS
  CID:      QmAbc456...
  TX ID:    1a2b3c4d...
  File pinned to IPFS.
  Encrypted with ML-KEM-768 (quantum-resistant)
```

The recipient retrieves and decrypts the file via the `qv_decapsulateShared` JSON-RPC method (available in the full API — see [07-rest-api.md](07-rest-api.md)).

## Next Steps

- [Stake QBIT and become a validator](04-staking.md)
- [Explore the web dashboard](05-dashboard.md)
- [Learn about the REST API](07-rest-api.md)
