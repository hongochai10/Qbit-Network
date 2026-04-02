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
    parser.add_argument("--cors-origin", default="",
                        help="Allowed CORS origin (e.g. 'https://app.example.com' or '*' for dev)")
    parser.add_argument("--no-verify-on-load", action="store_true",
                        help="Skip signature verification when loading chain from SQLite (faster startup)")
    parser.add_argument("--wallet-password", default="",
                        help="Password for wallet encryption (or set QBIT_WALLET_PASSWORD env var)")
    parser.add_argument("--dynamic-fee-activation", type=int, default=None,
                        help="Block height to activate EIP-1559 dynamic fees (0=genesis, default=disabled)")
    args = parser.parse_args()

    # Dynamic fee activation: CLI flag overrides env var, which overrides default
    if args.dynamic_fee_activation is not None:
        import qbit_network.config as _cfg
        import qbit_network.core.blockchain as _bc
        import qbit_network.core.consensus as _cons
        _cfg.DYNAMIC_FEE_ACTIVATION_HEIGHT = args.dynamic_fee_activation
        _bc.DYNAMIC_FEE_ACTIVATION_HEIGHT = args.dynamic_fee_activation
        _cons.DYNAMIC_FEE_ACTIVATION_HEIGHT = args.dynamic_fee_activation

    # Wallet password: CLI arg takes precedence, then env var
    wallet_password = args.wallet_password or os.environ.get("QBIT_WALLET_PASSWORD", "")

    validator = None
    if not args.no_validate:
        if args.wallet and os.path.exists(args.wallet):
            validator = Wallet.load(args.wallet, password=wallet_password)
        else:
            validator = Wallet.generate()
            if args.wallet:
                validator.save(args.wallet, password=wallet_password)

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
        wallet_password=wallet_password,
        cors_origins=args.cors_origin,
    )

    await node.start(validator_wallet=validator,
                     verify_on_load=not args.no_verify_on_load)

    try:
        while node._running:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        await node.stop()


if __name__ == "__main__":
    asyncio.run(main())
