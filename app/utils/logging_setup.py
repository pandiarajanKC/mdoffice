"""Structured logging: app.log, error.log, security.log.

Never logs passwords, tokens, secrets or confidential content.
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def _make_handler(path: str, level: int) -> RotatingFileHandler:
    handler = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_FORMAT))
    return handler


def configure_logging(app) -> None:
    log_dir = app.config.get("LOG_DIR", "logs")
    os.makedirs(log_dir, exist_ok=True)

    app.logger.setLevel(logging.INFO if not app.debug else logging.DEBUG)
    app.logger.addHandler(_make_handler(os.path.join(log_dir, "app.log"), logging.INFO))

    error_handler = _make_handler(os.path.join(log_dir, "error.log"), logging.ERROR)
    app.logger.addHandler(error_handler)

    security_logger = logging.getLogger("security")
    security_logger.setLevel(logging.INFO)
    security_logger.addHandler(_make_handler(os.path.join(log_dir, "security.log"), logging.INFO))
    security_logger.propagate = False
