"""Prometheus-compatible metrics collection for QBit Network.

Zero-dependency implementation — generates Prometheus text exposition format
without requiring the prometheus_client library.
"""

import time
import threading
from typing import Optional


class _Counter:
    """Monotonically increasing counter."""

    __slots__ = ("_name", "_help", "_labels", "_values", "_lock")

    def __init__(self, name: str, help_text: str, label_names: tuple[str, ...] = ()):
        self._name = name
        self._help = help_text
        self._labels = label_names
        self._values: dict[tuple[str, ...], float] = {}
        self._lock = threading.Lock()

    def inc(self, amount: float = 1, **labels: str) -> None:
        key = tuple(labels.get(l, "") for l in self._labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0) + amount

    def collect(self) -> list[str]:
        lines: list[str] = []
        lines.append(f"# HELP {self._name} {self._help}")
        lines.append(f"# TYPE {self._name} counter")
        with self._lock:
            if not self._labels:
                val = self._values.get((), 0)
                lines.append(f"{self._name} {val}")
            else:
                for key, val in sorted(self._values.items()):
                    label_str = ",".join(
                        f'{n}="{v}"' for n, v in zip(self._labels, key)
                    )
                    lines.append(f"{self._name}{{{label_str}}} {val}")
        return lines


class _Gauge:
    """Point-in-time gauge value."""

    __slots__ = ("_name", "_help", "_fn")

    def __init__(self, name: str, help_text: str, fn=None):
        self._name = name
        self._help = help_text
        self._fn = fn  # callable that returns the current value

    def collect(self) -> list[str]:
        val = self._fn() if self._fn else 0
        return [
            f"# HELP {self._name} {self._help}",
            f"# TYPE {self._name} gauge",
            f"{self._name} {val}",
        ]


class _Histogram:
    """Simple histogram with fixed buckets."""

    __slots__ = ("_name", "_help", "_labels", "_data", "_lock", "_buckets")

    _DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

    def __init__(self, name: str, help_text: str, label_names: tuple[str, ...] = (),
                 buckets: Optional[tuple[float, ...]] = None):
        self._name = name
        self._help = help_text
        self._labels = label_names
        self._buckets = buckets or self._DEFAULT_BUCKETS
        # key -> {"buckets": [counts...], "sum": float, "count": int}
        self._data: dict[tuple[str, ...], dict] = {}
        self._lock = threading.Lock()

    def _init_entry(self) -> dict:
        return {"buckets": [0] * len(self._buckets), "sum": 0.0, "count": 0}

    def observe(self, value: float, **labels: str) -> None:
        key = tuple(labels.get(l, "") for l in self._labels)
        with self._lock:
            if key not in self._data:
                self._data[key] = self._init_entry()
            entry = self._data[key]
            entry["sum"] += value
            entry["count"] += 1
            for i, bound in enumerate(self._buckets):
                if value <= bound:
                    entry["buckets"][i] += 1
                    break

    def collect(self) -> list[str]:
        lines: list[str] = []
        lines.append(f"# HELP {self._name} {self._help}")
        lines.append(f"# TYPE {self._name} histogram")
        with self._lock:
            for key, entry in sorted(self._data.items()):
                label_str = ""
                if self._labels:
                    label_str = ",".join(
                        f'{n}="{v}"' for n, v in zip(self._labels, key)
                    )
                cum = 0
                for i, bound in enumerate(self._buckets):
                    cum += entry["buckets"][i]
                    le_label = f'le="{bound}"'
                    if label_str:
                        lines.append(f'{self._name}_bucket{{{label_str},{le_label}}} {cum}')
                    else:
                        lines.append(f'{self._name}_bucket{{{le_label}}} {cum}')
                # +Inf bucket
                if label_str:
                    lines.append(f'{self._name}_bucket{{{label_str},le="+Inf"}} {entry["count"]}')
                    lines.append(f'{self._name}_sum{{{label_str}}} {entry["sum"]}')
                    lines.append(f'{self._name}_count{{{label_str}}} {entry["count"]}')
                else:
                    lines.append(f'{self._name}_bucket{{le="+Inf"}} {entry["count"]}')
                    lines.append(f'{self._name}_sum {entry["sum"]}')
                    lines.append(f'{self._name}_count {entry["count"]}')
        return lines


class MetricsRegistry:
    """Central metrics registry for a QBit node.

    Gauges are read lazily via callables (always up-to-date).
    Counters and histograms are updated as events occur.
    """

    def __init__(self):
        self._collectors: list[_Counter | _Gauge | _Histogram] = []

        # --- Counters ---
        self.blocks_produced_total = self._register(
            _Counter("qbit_blocks_produced_total", "Total blocks produced by this node"))
        self.tx_processed_total = self._register(
            _Counter("qbit_tx_processed_total", "Total transactions processed"))
        self.p2p_messages_total = self._register(
            _Counter("qbit_p2p_messages_total", "P2P messages by type",
                     label_names=("type",)))
        self.rpc_requests_total = self._register(
            _Counter("qbit_rpc_requests_total", "RPC requests by method",
                     label_names=("method",)))

        # --- Histogram ---
        self.rpc_request_duration = self._register(
            _Histogram("qbit_rpc_request_duration_seconds",
                       "RPC request duration in seconds",
                       label_names=("method",)))

        # --- Gauges (registered later via bind_node) ---
        self._node = None

    def bind_node(self, node) -> None:
        """Bind to a FullNode to provide live gauge values."""
        self._node = node
        self._register(_Gauge(
            "qbit_chain_height", "Current chain height",
            fn=lambda: node.blockchain.height))
        self._register(_Gauge(
            "qbit_peers_connected", "Number of connected peers",
            fn=lambda: node.p2p.peer_count()))
        self._register(_Gauge(
            "qbit_tx_pool_size", "Pending transactions in mempool",
            fn=lambda: len(node.blockchain.tx_pool)))

    def _register(self, collector):
        self._collectors.append(collector)
        return collector

    def generate_text(self) -> str:
        """Generate Prometheus text exposition format."""
        sections = []
        for c in self._collectors:
            sections.append("\n".join(c.collect()))
        return "\n".join(sections) + "\n"
