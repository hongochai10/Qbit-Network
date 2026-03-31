"""Unit tests for the Prometheus metrics module."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qbit_network.network.metrics import MetricsRegistry, _Counter, _Gauge, _Histogram


class TestCounter(unittest.TestCase):

    def test_inc_default(self):
        c = _Counter("test_total", "Test counter")
        c.inc()
        c.inc()
        lines = c.collect()
        self.assertIn("test_total 2", "\n".join(lines))

    def test_inc_amount(self):
        c = _Counter("test_total", "Test counter")
        c.inc(5)
        lines = c.collect()
        self.assertIn("test_total 5", "\n".join(lines))

    def test_labels(self):
        c = _Counter("rpc_total", "RPC calls", label_names=("method",))
        c.inc(method="getInfo")
        c.inc(method="getInfo")
        c.inc(method="transfer")
        text = "\n".join(c.collect())
        self.assertIn('rpc_total{method="getInfo"} 2', text)
        self.assertIn('rpc_total{method="transfer"} 1', text)

    def test_collect_format(self):
        c = _Counter("test_total", "A test counter")
        lines = c.collect()
        self.assertEqual(lines[0], "# HELP test_total A test counter")
        self.assertEqual(lines[1], "# TYPE test_total counter")


class TestGauge(unittest.TestCase):

    def test_gauge_fn(self):
        val = [42]
        g = _Gauge("test_gauge", "Test gauge", fn=lambda: val[0])
        text = "\n".join(g.collect())
        self.assertIn("test_gauge 42", text)
        val[0] = 100
        text = "\n".join(g.collect())
        self.assertIn("test_gauge 100", text)

    def test_gauge_no_fn(self):
        g = _Gauge("empty_gauge", "No fn")
        text = "\n".join(g.collect())
        self.assertIn("empty_gauge 0", text)


class TestHistogram(unittest.TestCase):

    def test_observe(self):
        h = _Histogram("req_duration", "Request duration",
                       buckets=(0.01, 0.1, 1.0))
        h.observe(0.005)
        h.observe(0.05)
        h.observe(0.5)
        text = "\n".join(h.collect())
        # 0.005 <= 0.01, so bucket 0.01 count = 1
        self.assertIn('req_duration_bucket{le="0.01"} 1', text)
        # 0.005 and 0.05 <= 0.1, so bucket 0.1 cumulative = 2
        self.assertIn('req_duration_bucket{le="0.1"} 2', text)
        # all 3 <= 1.0
        self.assertIn('req_duration_bucket{le="1.0"} 3', text)
        self.assertIn('req_duration_bucket{le="+Inf"} 3', text)
        self.assertIn("req_duration_count 3", text)

    def test_observe_with_labels(self):
        h = _Histogram("rpc_dur", "RPC duration", label_names=("method",),
                       buckets=(0.1, 1.0))
        h.observe(0.05, method="transfer")
        text = "\n".join(h.collect())
        self.assertIn('method="transfer"', text)
        self.assertIn("rpc_dur_count", text)


class TestMetricsRegistry(unittest.TestCase):

    def test_generate_text_without_node(self):
        reg = MetricsRegistry()
        text = reg.generate_text()
        # Should include counter definitions even without node binding
        self.assertIn("qbit_blocks_produced_total", text)
        self.assertIn("qbit_tx_processed_total", text)

    def test_generate_text_with_mock_node(self):
        class FakeBlockchain:
            height = 10
            tx_pool = [1, 2, 3]

        class FakeP2P:
            def peer_count(self):
                return 5

        class FakeNode:
            blockchain = FakeBlockchain()
            p2p = FakeP2P()

        reg = MetricsRegistry()
        reg.bind_node(FakeNode())
        text = reg.generate_text()
        self.assertIn("qbit_chain_height 10", text)
        self.assertIn("qbit_peers_connected 5", text)
        self.assertIn("qbit_tx_pool_size 3", text)

    def test_counter_increment_and_output(self):
        reg = MetricsRegistry()
        reg.blocks_produced_total.inc()
        reg.blocks_produced_total.inc()
        text = reg.generate_text()
        self.assertIn("qbit_blocks_produced_total 2", text)

    def test_p2p_message_counter_labels(self):
        reg = MetricsRegistry()
        reg.p2p_messages_total.inc(type="new_block")
        reg.p2p_messages_total.inc(type="new_block")
        reg.p2p_messages_total.inc(type="new_tx")
        text = reg.generate_text()
        self.assertIn('qbit_p2p_messages_total{type="new_block"} 2', text)
        self.assertIn('qbit_p2p_messages_total{type="new_tx"} 1', text)


if __name__ == "__main__":
    unittest.main()
