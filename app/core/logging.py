"""
Structured Logging Configuration

WHY STRUCTURED LOGGING?
  In distributed systems, logs are consumed by machines (ELK stack,
  Datadog, CloudWatch), not just humans. JSON-formatted logs with
  consistent fields (timestamp, level, service, request_id) enable:
  - Filtering & searching across thousands of instances
  - Correlation of requests across microservices
  - Alerting on error patterns
  - Performance monitoring via log analytics

  Plain text logs like "Something happened" are useless at scale.
  Structured logs like {"event": "url_created", "short_code": "abc123"}
  are queryable, indexable, and actionable.
"""

import logging
import json
import sys
from datetime import datetime, timezone
from typing import Any


class JSONFormatter(logging.Formatter):
    """
    Custom JSON log formatter for structured logging.

    Each log entry becomes a JSON object with consistent fields,
    making it parseable by log aggregation systems.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as a JSON string."""
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Include exception info if present
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Include any extra fields passed via `extra={}`
        if hasattr(record, "extra_data"):
            log_entry["data"] = record.extra_data

        return json.dumps(log_entry)


def setup_logging(level: str = "INFO") -> None:
    """
    Configure application-wide structured logging.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Clear existing handlers to avoid duplicate logs on reload
    root_logger.handlers.clear()

    # Stream handler with JSON formatting
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root_logger.addHandler(handler)

    # Suppress noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Get a named logger instance.

    WHY NAMED LOGGERS?
      Each module gets its own logger (e.g., "app.services.url_service").
      This lets you:
      - Filter logs by component
      - Set different log levels per module
      - Trace exactly where a log came from

    Args:
        name: Logger name, typically __name__ of the calling module.

    Returns:
        Configured logger instance.
    """
    return logging.getLogger(name)
