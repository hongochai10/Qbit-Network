#!/usr/bin/env python3
"""QBit Network — Production Load Benchmark Suite.

Measures:
  1. ML-DSA sign/verify throughput
  2. TX submission latency (p50, p95, p99)
  3. Block production throughput (blocks/sec, TX/sec)
  4. SQLite write performance (blocks persisted/sec)
  5. State trie rebuild time
  6. Memory usage under sustained load

Usage:
  python3 -m benchmarks.bench_production_load [--levels 1000 10000 100000]
"""
import argparse
import gc
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
import tracemalloc
from dataclasses import dataclass, field, asdict
from pathlib import Path

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qbit_network.core.wallet import Wallet
from qbit_network.core.blockchain import Blockchain
from qbit_network.core.transaction import Transaction, TxType
from qbit_network.crypto import MLDSA, sha3_256
from qbit_network.core.state_tree import StateTrie
from qbit_network.config import (
    MAX_TX_PER_BLOCK, CHAIN_ID, QUBIT_PER_QBIT,
)


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

@dataclass
class LatencyStats:
    count: int = 0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    min_ms: float = 0.0
    max_ms: float = 0.0
    mean_ms: float = 0.0

    @classmethod
    def from_samples(cls, samples_sec: list[float]) -> "LatencyStats":
        if not samples_sec:
            return cls()
        ms = [s * 1000 for s in samples_sec]
        ms.sort()
        n = len(ms)
        return cls(
            count=n,
            p50_ms=ms[int(n * 0.50)],
            p95_ms=ms[int(n * 0.95)] if n >= 20 else ms[-1],
            p99_ms=ms[int(n * 0.99)] if n >= 100 else ms[-1],
            min_ms=ms[0],
            max_ms=ms[-1],
            mean_ms=statistics.mean(ms),
        )


@dataclass
class BenchmarkResult:
    level: int = 0
    mldsa_sign_ops_sec: float = 0.0
    mldsa_verify_ops_sec: float = 0.0
    tx_submit_latency: LatencyStats = field(default_factory=LatencyStats)
    blocks_produced: int = 0
    block_production_sec: float = 0.0
    blocks_per_sec: float = 0.0
    tx_per_sec: float = 0.0
    sqlite_blocks_written: int = 0
    sqlite_write_sec: float = 0.0
    sqlite_blocks_per_sec: float = 0.0
    state_trie_entries: int = 0
    state_trie_rebuild_ms: float = 0.0
    peak_memory_mb: float = 0.0
    current_memory_mb: float = 0.0
    bottlenecks: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_transfer_tx(sender_wallet: Wallet, recipient_addr: str,
                      nonce: int, amount: int = 1000) -> Transaction:
    """Create a signed TRANSFER transaction."""
    tx = Transaction(
        tx_type=TxType.TRANSFER,
        sender=sender_wallet.address,
        recipient=recipient_addr,
        payload={"amount": amount},
        nonce=nonce,
        chain_id=CHAIN_ID,
        max_fee_per_weight=200,
        max_priority_fee=1,
    )
    tx.sign(sender_wallet.signing_sk, sender_wallet.signing_pk)
    return tx


def _make_notarize_tx(sender_wallet: Wallet, nonce: int,
                      doc_hash: str = None) -> Transaction:
    """Create a signed NOTARIZE transaction."""
    if doc_hash is None:
        doc_hash = sha3_256(f"bench-doc-{nonce}-{time.time()}".encode()).hex()
    tx = Transaction(
        tx_type=TxType.NOTARIZE,
        sender=sender_wallet.address,
        payload={"documentHash": doc_hash, "metadata": "benchmark"},
        nonce=nonce,
        chain_id=CHAIN_ID,
        max_fee_per_weight=200,
        max_priority_fee=1,
    )
    tx.sign(sender_wallet.signing_sk, sender_wallet.signing_pk)
    return tx


def _setup_chain(data_dir: str = None) -> tuple[Blockchain, Wallet, Wallet]:
    """Create a blockchain with a validator and a funded sender."""
    validator = Wallet.generate()
    sender = Wallet.generate()

    bc = Blockchain(data_dir=data_dir)
    bc.consensus.add_validator(validator.address, validator.signing_pk)
    bc.init_chain(validator.address, validator.signing_sk)

    # Register sender key so they can transact
    reg_tx = Transaction(
        tx_type=TxType.REGISTER_KEY,
        sender=sender.address,
        payload={"encryption_pk": sender.encryption_pk.hex()},
        nonce=0,
        chain_id=CHAIN_ID,
        max_fee_per_weight=200,
        max_priority_fee=1,
    )
    reg_tx.sign(sender.signing_sk, sender.signing_pk)
    ok, _ = bc.submit_tx(reg_tx)
    if not ok:
        # Fund sender first if needed — genesis gives validator funds
        # Transfer from validator to sender
        fund_tx = Transaction(
            tx_type=TxType.TRANSFER,
            sender=validator.address,
            recipient=sender.address,
            payload={"amount": 1000 * QUBIT_PER_QBIT},
            nonce=bc.get_nonce(validator.address),
            chain_id=CHAIN_ID,
            max_fee_per_weight=200,
            max_priority_fee=1,
        )
        fund_tx.sign(validator.signing_sk, validator.signing_pk)
        bc.submit_tx(fund_tx)
        bc.produce_block(validator.address, validator.signing_sk)

        # Retry registration
        reg_tx2 = Transaction(
            tx_type=TxType.REGISTER_KEY,
            sender=sender.address,
            payload={"encryption_pk": sender.encryption_pk.hex()},
            nonce=0,
            chain_id=CHAIN_ID,
            max_fee_per_weight=200,
            max_priority_fee=1,
        )
        reg_tx2.sign(sender.signing_sk, sender.signing_pk)
        bc.submit_tx(reg_tx2)

    bc.produce_block(validator.address, validator.signing_sk)

    # Fund sender generously
    fund_amount = 500_000 * QUBIT_PER_QBIT
    fund_tx = Transaction(
        tx_type=TxType.TRANSFER,
        sender=validator.address,
        recipient=sender.address,
        payload={"amount": fund_amount},
        nonce=bc.get_nonce(validator.address),
        chain_id=CHAIN_ID,
        max_fee_per_weight=200,
        max_priority_fee=1,
    )
    fund_tx.sign(validator.signing_sk, validator.signing_pk)
    bc.submit_tx(fund_tx)
    bc.produce_block(validator.address, validator.signing_sk)

    return bc, validator, sender


# ---------------------------------------------------------------------------
# Benchmark functions
# ---------------------------------------------------------------------------

def bench_mldsa_throughput(iterations: int = 500) -> tuple[float, float]:
    """Measure ML-DSA-65 sign and verify operations per second."""
    sk, pk = MLDSA.keygen()
    msg = b"benchmark-message-for-mldsa-throughput-test-" + os.urandom(32)

    # Sign benchmark
    t0 = time.perf_counter()
    sigs = []
    for _ in range(iterations):
        sigs.append(MLDSA.sign(sk, msg))
    sign_elapsed = time.perf_counter() - t0
    sign_ops = iterations / sign_elapsed

    # Verify benchmark
    t0 = time.perf_counter()
    for sig in sigs:
        MLDSA.verify(pk, msg, sig)
    verify_elapsed = time.perf_counter() - t0
    verify_ops = iterations / verify_elapsed

    return sign_ops, verify_ops


def bench_tx_submission(bc: Blockchain, sender: Wallet, validator: Wallet,
                        tx_count: int) -> LatencyStats:
    """Measure TX submission latency (submit_tx only, no mining)."""
    recipient = validator.address
    base_nonce = bc.get_nonce(sender.address)
    latencies = []

    for i in range(tx_count):
        tx = _make_transfer_tx(sender, recipient, base_nonce + i, amount=1)
        t0 = time.perf_counter()
        ok, msg = bc.submit_tx(tx)
        elapsed = time.perf_counter() - t0
        if ok:
            latencies.append(elapsed)

        # Mine periodically to keep pool from filling up and nonces fresh
        if (i + 1) % MAX_TX_PER_BLOCK == 0:
            bc.produce_block(validator.address, validator.signing_sk)

    # Mine remaining
    while bc.tx_pool:
        bc.produce_block(validator.address, validator.signing_sk)

    return LatencyStats.from_samples(latencies)


def bench_block_production(bc: Blockchain, sender: Wallet, validator: Wallet,
                           tx_count: int) -> tuple[int, float, float, float]:
    """Measure block production throughput with pre-loaded TX pool.

    Returns (blocks_produced, elapsed_sec, blocks_per_sec, tx_per_sec).
    """
    recipient = validator.address
    base_nonce = bc.get_nonce(sender.address)
    total_mined_tx = 0
    blocks_produced = 0

    # Pre-load transactions in batches, mine, measure
    batch_size = min(MAX_TX_PER_BLOCK, tx_count)
    remaining = tx_count
    nonce = base_nonce
    elapsed_total = 0.0

    while remaining > 0:
        # Fill pool
        batch = min(batch_size, remaining)
        for i in range(batch):
            tx = _make_transfer_tx(sender, recipient, nonce, amount=1)
            bc.submit_tx(tx)
            nonce += 1
        remaining -= batch

        # Mine and time it
        t0 = time.perf_counter()
        block = bc.produce_block(validator.address, validator.signing_sk)
        elapsed = time.perf_counter() - t0
        elapsed_total += elapsed

        if block:
            blocks_produced += 1
            total_mined_tx += len(block.transactions)

    blocks_per_sec = blocks_produced / elapsed_total if elapsed_total > 0 else 0
    tx_per_sec = total_mined_tx / elapsed_total if elapsed_total > 0 else 0

    return blocks_produced, elapsed_total, blocks_per_sec, tx_per_sec


def bench_sqlite_write(tx_count: int) -> tuple[int, float, float]:
    """Measure SQLite block persistence throughput.

    Returns (blocks_written, elapsed_sec, blocks_per_sec).
    """
    tmp_dir = tempfile.mkdtemp(prefix="qbit_bench_sqlite_")
    try:
        bc, validator, sender = _setup_chain(data_dir=tmp_dir)
        recipient = validator.address
        base_nonce = bc.get_nonce(sender.address)
        nonce = base_nonce
        remaining = tx_count
        blocks_written = 0
        batch_size = min(MAX_TX_PER_BLOCK, tx_count)

        # Pre-generate all transactions
        all_txs = []
        for i in range(tx_count):
            tx = _make_transfer_tx(sender, recipient, nonce + i, amount=1)
            all_txs.append(tx)

        nonce_cursor = nonce
        elapsed_total = 0.0
        tx_idx = 0

        while tx_idx < len(all_txs):
            batch_end = min(tx_idx + batch_size, len(all_txs))
            for j in range(tx_idx, batch_end):
                bc.submit_tx(all_txs[j])
            tx_idx = batch_end

            t0 = time.perf_counter()
            block = bc.produce_block(validator.address, validator.signing_sk)
            elapsed = time.perf_counter() - t0
            elapsed_total += elapsed

            if block:
                blocks_written += 1

        blocks_per_sec = blocks_written / elapsed_total if elapsed_total > 0 else 0
        return blocks_written, elapsed_total, blocks_per_sec
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def bench_state_trie_rebuild(entry_count: int) -> float:
    """Measure state trie root computation time for N entries.

    Returns elapsed_ms.
    """
    trie = StateTrie()
    for i in range(entry_count):
        key = f"balance:qv1{sha3_256(str(i).encode()).hex()}"
        value = (i * 1000).to_bytes(8, "big")
        trie.set(key, value)

    # Force computation
    gc.collect()
    t0 = time.perf_counter()
    _ = trie.root()
    elapsed = time.perf_counter() - t0
    return elapsed * 1000  # ms


def bench_memory_usage(bc: Blockchain, sender: Wallet, validator: Wallet,
                       tx_count: int) -> tuple[float, float]:
    """Measure peak and current memory under sustained load.

    Returns (peak_mb, current_mb).
    """
    tracemalloc.start()

    recipient = validator.address
    base_nonce = bc.get_nonce(sender.address)
    nonce = base_nonce
    remaining = tx_count
    batch_size = min(MAX_TX_PER_BLOCK, tx_count)

    while remaining > 0:
        batch = min(batch_size, remaining)
        for i in range(batch):
            tx = _make_transfer_tx(sender, recipient, nonce, amount=1)
            bc.submit_tx(tx)
            nonce += 1
        remaining -= batch
        bc.produce_block(validator.address, validator.signing_sk)

    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    return peak / (1024 * 1024), current / (1024 * 1024)


# ---------------------------------------------------------------------------
# Identify bottlenecks
# ---------------------------------------------------------------------------

def identify_bottlenecks(result: BenchmarkResult) -> list[str]:
    bottlenecks = []

    if result.mldsa_sign_ops_sec < 100:
        bottlenecks.append(
            f"ML-DSA sign throughput low ({result.mldsa_sign_ops_sec:.0f} ops/s) — "
            "PQC signing is CPU-bound"
        )
    if result.mldsa_verify_ops_sec < 200:
        bottlenecks.append(
            f"ML-DSA verify throughput low ({result.mldsa_verify_ops_sec:.0f} ops/s) — "
            "signature verification is CPU-bound"
        )
    if result.tx_submit_latency.p99_ms > 50:
        bottlenecks.append(
            f"TX submission p99 latency high ({result.tx_submit_latency.p99_ms:.1f}ms) — "
            "pool validation overhead"
        )
    if result.tx_per_sec > 0 and result.tx_per_sec < 500:
        bottlenecks.append(
            f"Block production TX throughput low ({result.tx_per_sec:.0f} TX/s) — "
            "state mutation or fee calc overhead"
        )
    if result.sqlite_blocks_per_sec > 0 and result.sqlite_blocks_per_sec < 10:
        bottlenecks.append(
            f"SQLite write throughput low ({result.sqlite_blocks_per_sec:.1f} blocks/s) — "
            "disk I/O or index overhead"
        )
    if result.state_trie_rebuild_ms > 1000:
        bottlenecks.append(
            f"State trie rebuild slow ({result.state_trie_rebuild_ms:.0f}ms) — "
            "Merkle computation overhead"
        )
    if result.peak_memory_mb > 500:
        bottlenecks.append(
            f"Peak memory high ({result.peak_memory_mb:.0f}MB) — "
            "in-memory index growth"
        )

    if not bottlenecks:
        bottlenecks.append("No significant bottlenecks detected at this load level")

    return bottlenecks


# ---------------------------------------------------------------------------
# Run full suite for one level
# ---------------------------------------------------------------------------

def run_benchmark(tx_count: int) -> BenchmarkResult:
    """Run the complete benchmark suite for a given TX count."""
    result = BenchmarkResult(level=tx_count)
    print(f"\n{'='*60}")
    print(f"  BENCHMARK LEVEL: {tx_count:,} transactions")
    print(f"{'='*60}")

    # 1. ML-DSA throughput (independent of TX count, use fixed iterations)
    crypto_iters = min(500, max(100, tx_count // 10))
    print(f"\n[1/6] ML-DSA sign/verify throughput ({crypto_iters} iterations)...")
    sign_ops, verify_ops = bench_mldsa_throughput(crypto_iters)
    result.mldsa_sign_ops_sec = sign_ops
    result.mldsa_verify_ops_sec = verify_ops
    print(f"  Sign:   {sign_ops:,.1f} ops/s")
    print(f"  Verify: {verify_ops:,.1f} ops/s")

    # 2. TX submission latency
    print(f"\n[2/6] TX submission latency ({tx_count:,} TXs)...")
    bc, validator, sender = _setup_chain()
    result.tx_submit_latency = bench_tx_submission(bc, sender, validator, tx_count)
    ls = result.tx_submit_latency
    print(f"  Submitted: {ls.count:,} TXs")
    print(f"  p50:  {ls.p50_ms:.3f}ms")
    print(f"  p95:  {ls.p95_ms:.3f}ms")
    print(f"  p99:  {ls.p99_ms:.3f}ms")
    print(f"  Mean: {ls.mean_ms:.3f}ms")
    del bc

    # 3. Block production throughput (in-memory)
    print(f"\n[3/6] Block production throughput ({tx_count:,} TXs, in-memory)...")
    bc, validator, sender = _setup_chain()
    blocks, elapsed, bps, tps = bench_block_production(bc, sender, validator, tx_count)
    result.blocks_produced = blocks
    result.block_production_sec = elapsed
    result.blocks_per_sec = bps
    result.tx_per_sec = tps
    print(f"  Blocks:    {blocks:,} in {elapsed:.2f}s")
    print(f"  Blocks/s:  {bps:,.1f}")
    print(f"  TX/s:      {tps:,.1f}")
    del bc

    # 4. SQLite write performance
    print(f"\n[4/6] SQLite write performance ({tx_count:,} TXs)...")
    sq_blocks, sq_elapsed, sq_bps = bench_sqlite_write(tx_count)
    result.sqlite_blocks_written = sq_blocks
    result.sqlite_write_sec = sq_elapsed
    result.sqlite_blocks_per_sec = sq_bps
    print(f"  Blocks:    {sq_blocks:,} in {sq_elapsed:.2f}s")
    print(f"  Blocks/s:  {sq_bps:,.1f}")

    # 5. State trie rebuild
    # Use entry count proportional to TX count (each TX creates ~2 state entries)
    trie_entries = min(tx_count * 2, 200_000)
    print(f"\n[5/6] State trie rebuild ({trie_entries:,} entries)...")
    trie_ms = bench_state_trie_rebuild(trie_entries)
    result.state_trie_entries = trie_entries
    result.state_trie_rebuild_ms = trie_ms
    print(f"  Root computation: {trie_ms:.1f}ms")

    # 6. Memory usage under sustained load
    print(f"\n[6/6] Memory usage under sustained load ({tx_count:,} TXs)...")
    bc, validator, sender = _setup_chain()
    peak_mb, current_mb = bench_memory_usage(bc, sender, validator, tx_count)
    result.peak_memory_mb = peak_mb
    result.current_memory_mb = current_mb
    print(f"  Peak:    {peak_mb:.1f} MB")
    print(f"  Current: {current_mb:.1f} MB")
    del bc

    # Bottleneck identification
    result.bottlenecks = identify_bottlenecks(result)
    print(f"\n  Bottlenecks:")
    for b in result.bottlenecks:
        print(f"    - {b}")

    return result


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_json_report(results: list[BenchmarkResult], output_path: str):
    """Write results as JSON."""
    data = {
        "benchmark": "QBit Network Production Load",
        "version": "0.8.0",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "results": [asdict(r) for r in results],
    }
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"\nJSON report: {output_path}")


def generate_markdown_report(results: list[BenchmarkResult], output_path: str):
    """Write results as a markdown report."""
    lines = [
        "# QBit Network — Production Load Benchmark Report",
        "",
        f"**Generated:** {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}  ",
        f"**Version:** v0.8.0  ",
        f"**Backend:** SQLite (WAL mode)  ",
        f"**PQC:** ML-DSA-65 (signing), ML-KEM-768 (encryption)  ",
        f"**Max TX/block:** {MAX_TX_PER_BLOCK}",
        "",
        "---",
        "",
    ]

    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | " + " | ".join(f"{r.level:,} TX" for r in results) + " |")
    lines.append("| --- | " + " | ".join("---:" for _ in results) + " |")

    rows = [
        ("ML-DSA sign (ops/s)", [f"{r.mldsa_sign_ops_sec:,.0f}" for r in results]),
        ("ML-DSA verify (ops/s)", [f"{r.mldsa_verify_ops_sec:,.0f}" for r in results]),
        ("TX submit p50 (ms)", [f"{r.tx_submit_latency.p50_ms:.3f}" for r in results]),
        ("TX submit p95 (ms)", [f"{r.tx_submit_latency.p95_ms:.3f}" for r in results]),
        ("TX submit p99 (ms)", [f"{r.tx_submit_latency.p99_ms:.3f}" for r in results]),
        ("Block production (blocks/s)", [f"{r.blocks_per_sec:,.1f}" for r in results]),
        ("Block production (TX/s)", [f"{r.tx_per_sec:,.0f}" for r in results]),
        ("SQLite write (blocks/s)", [f"{r.sqlite_blocks_per_sec:,.1f}" for r in results]),
        ("State trie rebuild (ms)", [f"{r.state_trie_rebuild_ms:.1f}" for r in results]),
        ("Peak memory (MB)", [f"{r.peak_memory_mb:.1f}" for r in results]),
    ]
    for label, vals in rows:
        lines.append(f"| {label} | " + " | ".join(vals) + " |")

    lines.append("")

    # Per-level detail
    for r in results:
        lines.append(f"## {r.level:,} TX Detail")
        lines.append("")
        lines.append(f"- **Blocks produced:** {r.blocks_produced:,}")
        lines.append(f"- **Block production time:** {r.block_production_sec:.2f}s")
        lines.append(f"- **SQLite blocks written:** {r.sqlite_blocks_written:,} in {r.sqlite_write_sec:.2f}s")
        lines.append(f"- **State trie entries:** {r.state_trie_entries:,}")
        lines.append(f"- **Memory:** {r.peak_memory_mb:.1f}MB peak, {r.current_memory_mb:.1f}MB current")
        lines.append("")
        lines.append("**Bottlenecks:**")
        for b in r.bottlenecks:
            lines.append(f"- {b}")
        lines.append("")

    with open(output_path, "w") as f:
        f.write("\n".join(lines))
    print(f"Markdown report: {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="QBit Network Production Load Benchmark Suite"
    )
    parser.add_argument(
        "--levels", nargs="+", type=int, default=[1000, 10000],
        help="TX counts to benchmark (default: 1000 10000). Use 100000 for full suite."
    )
    parser.add_argument(
        "--output-dir", type=str, default="benchmarks/results",
        help="Directory for output reports"
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("  QBit Network — Production Load Benchmark Suite")
    print(f"  Levels: {', '.join(f'{l:,}' for l in args.levels)} TX")
    print("=" * 60)

    results = []
    for level in args.levels:
        gc.collect()
        result = run_benchmark(level)
        results.append(result)

    # Generate reports
    ts = time.strftime("%Y%m%d_%H%M%S")
    json_path = str(output_dir / f"bench_{ts}.json")
    md_path = str(output_dir / f"bench_{ts}.md")

    generate_json_report(results, json_path)
    generate_markdown_report(results, md_path)

    print(f"\nDone. Reports saved to {output_dir}/")
    return results


if __name__ == "__main__":
    main()
