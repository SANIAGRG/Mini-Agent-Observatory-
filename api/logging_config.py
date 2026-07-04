import logging
import json
import sys


class JsonFormatter(logging.Formatter):
    """Formats every log line as a single JSON object, so logs can be
    parsed by a real log aggregator instead of relying on human-readable
    text formatting."""

    def format(self, record):
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
