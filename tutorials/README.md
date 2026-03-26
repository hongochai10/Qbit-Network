# QBit Network Tutorials

Welcome to QBit Network — a Post-Quantum Cryptography blockchain for document notarization and encrypted file sharing.

## For Users

1. [Getting Started](01-getting-started.md) — Install, run a node, verify it works
2. [Wallets and Transfers](02-wallets-and-transfers.md) — Create a wallet, send QBIT
3. [Document Notarization](03-notarization.md) — Notarize and verify documents
4. [Staking and Validators](04-staking.md) — Stake QBIT, become a validator
5. [Web Dashboard](05-dashboard.md) — Using the NextJS explorer

## For Developers

6. [Architecture Overview](06-architecture.md) — How QBit works under the hood
7. [REST API Guide](07-rest-api.md) — Complete API reference with examples
8. [Fee System (EIP-1559)](08-fees.md) — How dynamic fees work
9. [Running a Testnet](09-testnet.md) — Multi-node testnet setup
10. [Security Model](10-security.md) — PQC cryptography, audit history, threat model

## Quick Reference

| Task | Tutorial |
|------|---------|
| Install and run first node | [01-getting-started.md](01-getting-started.md) |
| Create a wallet | [02-wallets-and-transfers.md](02-wallets-and-transfers.md) |
| Send QBIT to another address | [02-wallets-and-transfers.md](02-wallets-and-transfers.md#send-qbit) |
| Notarize a document | [03-notarization.md](03-notarization.md) |
| Verify a document is on-chain | [03-notarization.md](03-notarization.md#verify-a-document) |
| Stake and earn rewards | [04-staking.md](04-staking.md) |
| Use the web dashboard | [05-dashboard.md](05-dashboard.md) |
| Call the REST API | [07-rest-api.md](07-rest-api.md) |
| Understand fees | [08-fees.md](08-fees.md) |
| Run a multi-node testnet | [09-testnet.md](09-testnet.md) |

## About QBit Network

QBit Network uses NIST-standardized post-quantum cryptography to replace all quantum-vulnerable algorithms:

| Classical Algorithm | QBit Replacement |
|--------------------|-----------------|
| ECDSA signatures | ML-DSA-65 (FIPS 204) |
| ECDH key exchange | ML-KEM-768 (FIPS 203) |
| SHA-256 hashing | SHA3-256 (FIPS 202) |
| RSA/secp256k1 keypairs | Dual ML-DSA + ML-KEM keypairs |

Current version: **v0.6.0** — 1358 tests, 17 audit rounds, 0 open issues.
