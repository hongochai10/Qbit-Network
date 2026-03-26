# Security Model

## Why Post-Quantum?

Classical blockchains use ECDSA for transaction signatures and ECDH for key exchange. Both depend on the difficulty of solving elliptic curve discrete logarithm problems. Shor's algorithm, run on a sufficiently large fault-tolerant quantum computer, solves this problem in polynomial time — meaning a quantum attacker could derive private keys from public keys and forge signatures.

This threat is not theoretical. Cryptographers estimate that a quantum computer capable of breaking 256-bit ECDSA would require roughly 4,000 logical qubits (with billions of physical qubits accounting for error correction). Timelines are uncertain, but documents notarized today need to remain verifiable for decades. A blockchain that becomes forgeable in 15 years is not a reliable notarization system.

NIST ran a multi-year competition to standardize post-quantum algorithms. The first standards were published in 2024. QBit Network uses only these standardized algorithms — no experimental or proprietary cryptography.

## QBit's Cryptographic Stack

| Algorithm | Standard | NIST Security Level | Purpose |
|-----------|----------|-------------------|---------|
| ML-DSA-65 | FIPS 204 | Level 3 (equivalent to ~192-bit classical) | All transaction and block signatures; P2P authentication |
| ML-KEM-768 | FIPS 203 | Level 3 | Key encapsulation for encrypted file sharing; P2P session keys |
| SHA3-256 | FIPS 202 | 128-bit quantum security | Hashing, address derivation, Merkle tree |
| AES-256-GCM | FIPS 197 | 128-bit quantum security | Symmetric encryption of files and P2P transport |

NIST Level 3 means the algorithm is estimated to require at least as much computational effort to break as brute-forcing AES-192, even with a quantum computer.

## Key Sizes

One cost of post-quantum security is larger keys and signatures:

| Algorithm | Public Key | Secret Key | Signature or Ciphertext |
|-----------|-----------|-----------|------------------------|
| ML-DSA-65 | 1,952 bytes | 4,032 bytes | 3,309 bytes (signature) |
| ML-KEM-768 | 1,184 bytes | 2,400 bytes | 1,088 bytes (ciphertext) |
| ECDSA (secp256k1, for comparison) | 33 bytes | 32 bytes | ~72 bytes |

ML-DSA-65 signatures are ~46x larger than ECDSA. This is why transaction size limits and block weight limits exist — the chain would grow faster without them. The transaction payload size limit is 8 KB, and the maximum block size is 5 MB.

## SecureBytes

Private key material is sensitive: if a secret key is paged to disk or left in a core dump, an attacker can read it. Python's standard `bytes` type is immutable — you cannot zero it when you are done, and the garbage collector may defer cleanup.

QBit uses `SecureBytes` (in `qbit_network/crypto/secure_bytes.py`), a ctypes-backed mutable buffer that can be explicitly zeroed:

- `zero()` overwrites all bytes with zeros via `ctypes.memset`
- Context manager (`with wallet: ...`) zeros keys on exit
- `Wallet.close()` zeros both the signing and encryption secret keys
- scrypt-derived keys and decrypted plaintext buffers are zeroed after use
- Graceful fallback to `bytearray` with best-effort zeroing if ctypes is unavailable

## Wallet Encryption

Wallet files are encrypted at rest using:

1. Password → scrypt (N=16,384, r=8, p=1) → 32-byte derived key
2. AES-256-GCM encrypts the concatenated secret keys with the address as AAD (additional authenticated data)
3. The GCM authentication tag detects wrong passwords or file tampering
4. The address is re-derived from the decrypted signing key and compared to the stored address

Wallet files are written atomically (tempfile + `os.replace`) with permissions 0o600 (owner read/write only).

## P2P Authentication

Before exchanging any chain data, two nodes perform a 4-step ML-DSA mutual authentication:

1. Initiator sends `hello_auth`: a random 32-byte challenge, their signing public key, and a self-signed proof over `QBIT_AUTH_v2:challenge:address`
2. Responder verifies the proof (verify-before-sign), then signs the challenge and sends a counter-challenge
3. Initiator verifies the responder's signature and sends a signed response to the counter-challenge
4. Both sides mark the connection authenticated

The domain prefix `QBIT_AUTH_v2:` in the signature prevents cross-protocol reuse of signatures from other contexts.

After authentication, an ML-KEM-768 session key is established and all subsequent P2P messages are encrypted with AES-256-GCM. A node that fails authentication is disconnected immediately with no fallback to unauthenticated mode.

## Replay and Replay Prevention

Each transaction includes:
- `chainId` — prevents replay on different chains
- `nonce` — sequential per sender, prevents replay of the same transaction
- `timestamp` — must be within [-24 hours, +5 minutes] of current time

Cross-block replay is prevented by checking all transaction IDs against the full confirmed transaction index before accepting a block.

## Key Revocation

If a private key is compromised, the owner can permanently revoke it on-chain using a `REVOKE_KEY` transaction:

```json
{
  "type": "REVOKE_KEY",
  "payload": {
    "key_type": "signing",
    "reason": "compromised"
  }
}
```

Effects:
- Revoking a **signing key** blocks the address from submitting any further transactions
- Revoking an **encryption key** marks it in the registry so SHARE operations can filter it
- Revoking a **validator key** removes the validator from the active set

Revocations are permanent and cannot be reversed. The genesis validator's signing key cannot be revoked.

## TLS for RPC Connections

The RPC server supports TLS to protect the auth token and response data in transit:

```bash
python3 run_node.py --tls-auto --tls-hostname mynode.example.com
```

`--tls-auto` generates a self-signed ECC (P-256) certificate and renews it automatically before expiry. For production, provide a CA-signed certificate with `--tls-cert` and `--tls-key`.

When connecting from the CLI to a TLS-enabled node:

```bash
python3 cli/qbit.py notarize doc.pdf \
  --rpc https://mynode.example.com:8545 \
  --token YOUR_TOKEN
```

The `--insecure` flag disables certificate verification (testing only — this exposes your token to MITM attacks).

## Rate Limiting

Token bucket rate limiting protects against resource exhaustion:

**P2P:** 20 messages/second sustained, 100-message burst per peer. Peers are disconnected after 3 violations.

**RPC:** 10 requests/second sustained, 50-request burst per client IP. Returns HTTP 429 on violation. Localhost is exempt in development.

**WebSocket:** 10 messages/second per client, max 100 concurrent connections, max 10 subscriptions per client.

**Pool:** The transaction pool is capped at 10,000 pending transactions.

**Block limits:** Max 200 transactions per block, max 5 MB block size, max 8 KB per transaction payload.

## Peer Reputation

Peers are scored on 8 event types:

| Event | Score Change |
|-------|-------------|
| valid_block | +10 |
| valid_tx | +1 |
| invalid_block | -50 |
| invalid_tx | -10 |
| auth_failed | -100 |
| timeout | -5 |
| rate_limited | -20 |
| protocol_error | -30 |

Peers start at score 100. A score at or below -100 triggers an automatic ban. Scores decay by 0.99x per minute, so old good behavior fades but so does old bad behavior.

## Audit History

QBit Network has completed 17 rounds of security audit covering cryptographic correctness, input validation, protocol security, resource exhaustion, concurrency, persistence, and dPoS security.

| Round | Version | Focus | Issues Found |
|-------|---------|-------|-------------|
| 1 | v0.1.0 | Basic correctness | 14 |
| 2 | v0.1.0 | Deep crypto and protocol | 21 |
| 3 | v0.1.0 | Line-by-line review | 16 |
| 4 | v0.1.x | Regression analysis | 3 |
| 5 | v0.2.0 | Automated security agent | 21 |
| 6 | v0.2.0 | Red team adversarial | 9 |
| 7 | v0.2.0 | Fix regression and edge cases | 11 |
| 8 | v0.2.0 | Module consistency | 4 |
| 9 | v0.2.0 | Semantic and protocol correctness | 5 |
| 10 | v0.2.0 | Fork, TLS, CLI, store | 7 |
| 11 | v0.2.1 | Rate limiting and auth baseline | 9 |
| 12 | v0.2.1 | P2P auth, Docker, store/share | 9 |
| 13 Sprint 1 | v0.3.0 | HELLO_AUTH, REGISTER_VALIDATOR, rate limiting, CI | 14 |
| 13 Sprint 2 | v0.3.0 | SQLite-primary, REVOKE_KEY, REST API, WebSocket | 16 |
| 14 | v0.4.0 | dPoS, epochs, slashing, P2P encryption, dedup | 9 |
| 15 | v0.4.0 | SecureBytes, TLS auto-provisioning, reputation, pruning | 5 |
| 16 | v0.5.0 | Financial layer: TRANSFER, fees, rewards, supply | 5 |
| 17 | v0.6.0 | EIP-1559 and auth bypass (CRITICAL) | 2 |

Total: 197+ issues found and resolved. **0 open issues.**

The Round 17 CRITICAL finding was an authentication bypass vulnerability. It was identified, fixed, and verified before release. See `tracker/AUDIT_LOG.md` for the complete finding log.

## Accepted Risks

The following are known limitations that have been accepted:

| Risk | Notes |
|------|-------|
| Transaction pool not persisted across restarts | Pending transactions are lost on node restart. WAL-based persistence is planned. |
| No block finality checkpoint | There is no absolute finality mechanism. The longest chain always wins within the `MAX_REORG_DEPTH = 32` block limit. |
| Sybil/Eclipse attacks (residual) | HELLO_AUTH and reputation scoring raise the bar. Full Sybil resistance requires stake bonding for P2P admission. |

## Wallet Security Best Practices

1. **Use a strong password.** The scrypt parameters (N=16,384) provide good protection against brute force, but a weak password negates this.

2. **Back up your wallet file.** The wallet file (`wallets/qv1YOUR_ADDRESS.json`) is the only copy of your private keys. Losing it means losing access to your address permanently.

3. **Keep your RPC token secret.** Anyone with your token can create transactions from wallets loaded on your node. Use TLS when the node is accessible over a network.

4. **Register your encryption key promptly.** After creating a wallet, run `python3 cli/qbit.py wallet create --register` to publish your encryption key on-chain. Without this, others cannot send you encrypted files.

5. **Monitor for slashing if you are a validator.** Set up alerting on `GET /api/v1/slashing-events`. If you are slashed, investigate the root cause (usually a node crash causing duplicate block production) before re-staking.

## Further Reading

- [`docs/SECURITY.md`](../docs/SECURITY.md) — full threat model and control list
- [`tracker/AUDIT_LOG.md`](../tracker/AUDIT_LOG.md) — complete finding log for all 17 audit rounds
- [`docs/PROTOCOL.md`](../docs/PROTOCOL.md) — wire format specification including the P2P handshake
- [`docs/PAPER.md`](../docs/PAPER.md) — academic paper with formal analysis and benchmarks
