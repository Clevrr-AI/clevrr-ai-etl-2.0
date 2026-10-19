"""Structured logs: one JSON object per line on stdout (Cloud Logging reads `severity`).

Never log a credential. Log an exception's type and a short message, not the provider's
request URL (legacy logged URLs that carried access tokens).
"""

from __future__ import annotations

import logging
import sys

import structlog
from structlog.typing import EventDict, WrappedLogger


def _severity(_: WrappedLogger, method: str, event: EventDict) -> EventDict:
    event["severity"] = method.upper()
    return event


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    # httpx logs every request URL as plain text at INFO; the ETL logs its own JSON events.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _severity,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
