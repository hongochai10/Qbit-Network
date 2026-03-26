"""QBit SDK data models — pure dataclasses, no external dependencies."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Event:
    """A blockchain event emitted by a transaction."""
    type: str
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        return cls(type=d.get("type", ""), data=d.get("data", {}))


@dataclass
class Receipt:
    """Transaction receipt with execution status and events."""
    tx_id: str
    status: str
    fee_paid: int
    block_index: int
    tx_index: int
    events: list[Event] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Receipt":
        events = [Event.from_dict(e) for e in d.get("events", [])]
        return cls(
            tx_id=d.get("tx_id", ""),
            status=d.get("status", ""),
            fee_paid=d.get("fee_paid", 0),
            block_index=d.get("block_index", 0),
            tx_index=d.get("tx_index", 0),
            events=events,
        )


@dataclass
class Transaction:
    """Blockchain transaction."""
    tx_id: str
    tx_type: str
    sender: str
    recipient: str
    nonce: int
    timestamp: float
    payload: dict[str, Any] = field(default_factory=dict)
    signature: str = ""
    public_key: str = ""
    max_fee_per_weight: int = 0
    max_priority_fee: int = 0
    block_index: int | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "Transaction":
        return cls(
            tx_id=d.get("tx_id", ""),
            tx_type=d.get("tx_type", ""),
            sender=d.get("sender", ""),
            recipient=d.get("recipient", ""),
            nonce=d.get("nonce", 0),
            timestamp=d.get("timestamp", 0.0),
            payload=d.get("payload", {}),
            signature=d.get("signature", ""),
            public_key=d.get("public_key", ""),
            max_fee_per_weight=d.get("max_fee_per_weight", 0),
            max_priority_fee=d.get("max_priority_fee", 0),
            block_index=d.get("block_index"),
        )


@dataclass
class Block:
    """Blockchain block."""
    index: int
    timestamp: float
    previous_hash: str
    merkle_root: str
    state_root: str
    receipts_root: str
    base_fee: int
    validator: str
    signature: str
    block_hash: str
    transactions: list[Transaction] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Block":
        txs = [Transaction.from_dict(t) for t in d.get("transactions", [])]
        return cls(
            index=d.get("index", 0),
            timestamp=d.get("timestamp", 0.0),
            previous_hash=d.get("previous_hash", ""),
            merkle_root=d.get("merkle_root", ""),
            state_root=d.get("state_root", ""),
            receipts_root=d.get("receipts_root", ""),
            base_fee=d.get("base_fee", 0),
            validator=d.get("validator", ""),
            signature=d.get("signature", ""),
            block_hash=d.get("block_hash", ""),
            transactions=txs,
        )


@dataclass
class Wallet:
    """Wallet information returned from create_wallet."""
    address: str
    signing_pk: str
    encryption_pk: str

    @classmethod
    def from_dict(cls, d: dict) -> "Wallet":
        return cls(
            address=d.get("address", ""),
            signing_pk=d.get("signing_pk", ""),
            encryption_pk=d.get("encryption_pk", ""),
        )


@dataclass
class NodeInfo:
    """Node information."""
    version: str
    chain_height: int
    pending_txs: int
    peers: int
    validator: str | None
    validators: list[str] = field(default_factory=list)
    registered_validators: list[str] = field(default_factory=list)
    wallets: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> "NodeInfo":
        return cls(
            version=d.get("version", ""),
            chain_height=d.get("chain_height", 0),
            pending_txs=d.get("pending_txs", 0),
            peers=d.get("peers", 0),
            validator=d.get("validator"),
            validators=d.get("validators", []),
            registered_validators=d.get("registered_validators", []),
            wallets=d.get("wallets", 0),
        )


@dataclass
class BalanceInfo:
    """Balance information for an address."""
    address: str
    balance: int
    formatted: str

    @classmethod
    def from_dict(cls, d: dict) -> "BalanceInfo":
        return cls(
            address=d.get("address", ""),
            balance=d.get("balance", 0),
            formatted=d.get("formatted", ""),
        )


@dataclass
class SupplyInfo:
    """Token supply information."""
    total_minted: int
    total_burned: int
    circulating: int
    max_supply: int
    formatted_circulating: str
    fee_model: str

    @classmethod
    def from_dict(cls, d: dict) -> "SupplyInfo":
        return cls(
            total_minted=d.get("total_minted", 0),
            total_burned=d.get("total_burned", 0),
            circulating=d.get("circulating", 0),
            max_supply=d.get("max_supply", 0),
            formatted_circulating=d.get("formatted_circulating", ""),
            fee_model=d.get("fee_model", "fixed"),
        )


@dataclass
class FeeInfo:
    """Dynamic fee information."""
    base_fee: int
    next_base_fee: int
    suggested_priority_fee: int
    weights: dict[str, int] = field(default_factory=dict)
    estimated_fees: dict[str, dict[str, int]] = field(default_factory=dict)
    fee_model: str = "fixed"

    @classmethod
    def from_dict(cls, d: dict) -> "FeeInfo":
        return cls(
            base_fee=d.get("base_fee", 0),
            next_base_fee=d.get("next_base_fee", 0),
            suggested_priority_fee=d.get("suggested_priority_fee", 0),
            weights=d.get("weights", {}),
            estimated_fees=d.get("estimated_fees", {}),
            fee_model=d.get("fee_model", "fixed"),
        )


@dataclass
class StateProof:
    """Merkle state proof for an account key."""
    key: str
    value: str
    proof: list[str] = field(default_factory=list)
    state_root: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "StateProof":
        return cls(
            key=d.get("key", ""),
            value=d.get("value", ""),
            proof=d.get("proof", []),
            state_root=d.get("state_root", ""),
        )


@dataclass
class VerifyResult:
    """Document verification result."""
    verified: bool
    proof: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "VerifyResult":
        return cls(
            verified=d.get("verified", False),
            proof=d.get("proof"),
        )


@dataclass
class Webhook:
    """Registered webhook."""
    id: str
    url: str
    events: list[str] = field(default_factory=list)
    created_at: float = 0.0
    status: str = "active"
    consecutive_failures: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> "Webhook":
        return cls(
            id=d.get("id", ""),
            url=d.get("url", ""),
            events=d.get("events", []),
            created_at=d.get("created_at", 0.0),
            status=d.get("status", "active"),
            consecutive_failures=d.get("consecutive_failures", 0),
        )
