"""JSON log lines on stdout. Tool result content is never logged."""

import json
import logging
import sys
from datetime import UTC, datetime

_EXTRA = ("episode_id", "tool", "ok")
LOGGER_NAME = "crucible.server"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in _EXTRA:
            if hasattr(record, key):
                data[key] = getattr(record, key)
        return json.dumps(data, sort_keys=True)


def configure_logging(level: str) -> logging.Logger:
    """Attach one JSON stdout handler to the server logger (safe to call many times)."""
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False
    return logger


__all__ = ["LOGGER_NAME", "JsonFormatter", "configure_logging"]
