#!/usr/bin/env python3
"""QBit Network PQC Blockchain - Full Node."""
import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qbit_network.node import FullNode
from qbit_network.core.wallet import Wallet

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)


async def main():
    parser = argparse.ArgumentParser(description="QBit Network PQC Blockchain Node")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--p2p-port", type=int, default=9000)
    parser.add_argument("--rpc-port", type=int, default=8545)
    parser.add_argument("--data-dir", default="")
    parser.add_argument("--peers", nargs="*", default=[])
    parser.add_argument("--wallet", default="", help="Path to validator wallet JSON")
    parser.add_argument("--no-validate", action="store_true")
    parser.add_argument("--rpc-token", default="", help="RPC auth token (auto-generated if empty)")
    parser.add_argument("--tls-cert", default="", help="Path to TLS certificate PEM file")
    parser.add_argument("--tls-key", default="", help="Path to TLS private key PEM file")
    parser.add_argument("--tls-self-signed", action="store_true",
                        help="Generate self-signed TLS cert (development only)")
    parser.add_argument("--tls-auto", action="store_true",
                        help="Auto-generate and manage self-signed TLS cert in data_dir/tls/")
    parser.add_argument("--tls-hostname", default="localhost",
                        help="Hostname for TLS cert CN/SAN (default: localhost)")
    args = parser.parse_args()

    validator = None
    if not args.no_validate:
        if args.wallet and os.path.exists(args.wallet):
            validator = Wallet.load(args.wallet)
        else:
            validator = Wallet.generate()
            if args.wallet:
                validator.save(args.wallet)

    node = FullNode(
        host=args.host,
        p2p_port=args.p2p_port,
        rpc_port=args.rpc_port,
        data_dir=args.data_dir,
        bootstrap=args.peers,
        rpc_token=args.rpc_token,
        tls_cert=args.tls_cert,
        tls_key=args.tls_key,
        tls_self_signed=args.tls_self_signed,
        tls_auto=args.tls_auto,
        tls_hostname=args.tls_hostname,
    )

    await node.start(validator_wallet=validator)

    try:
        while node._running:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        await node.stop()


if __name__ == "__main__":
    asyncio.run(main())
