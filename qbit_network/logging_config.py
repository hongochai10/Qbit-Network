"""Structured JSON logging configuration for QBit Network.

Call ``configure_logging()`` once at node startup to switch all
``qbit_network.*`` loggers to structured JSON output on stdout.
"""

import json
import logging
import time
from typing import Optional


class JSONFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            entry["exception"] = self.formatException(record.exc_info)
        # Merge any extra structured fields passed via ``extra=``
        for key in ("node_id", "peer", "tx_hash", "block_height", "method"):
            val = getattr(record, key, None)
            if val is not None:
                entry[key] = val
        return json.dumps(entry, default=str)


def configure_logging(
    level: int = logging.INFO,
    json_output: bool = True,
    node_id: Optional[str] = None,
) -> None:
    """Configure the root ``qbit_network`` logger.

    Args:
        level: Log level (default INFO).
        json_output: If True, use JSON formatter; otherwise plain text.
        node_id: Optional node identifier added to every log line.
    """
    root = logging.getLogger("qbit_network")
    root.setLevel(level)

    # Avoid duplicate handlers on repeated calls
    root.handlers.clear()

    handler = logging.StreamHandler()
    if json_output:
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s — %(message)s"
        ))
    root.addHandler(handler)

    if node_id:
        old_factory = logging.getLogRecordFactory()

        def record_factory(*args, **kwargs):
            record = old_factory(*args, **kwargs)
            record.node_id = node_id
            return record

        logging.setLogRecordFactory(record_factory)
