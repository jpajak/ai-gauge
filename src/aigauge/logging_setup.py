from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import app_data_dir

LOGGER_NAME = "aigauge"
_LOG_FILENAME = "ai-gauge.log"


def log_path() -> Path:
    return app_data_dir() / _LOG_FILENAME


def setup_logging() -> logging.Logger:
    """Initialize a rotating file logger at %APPDATA%/ai-gauge/ai-gauge.log.

    Idempotent — safe to call multiple times. Returns the package logger so
    callers can write to it directly without going through getLogger().
    """
    logger = logging.getLogger(LOGGER_NAME)
    if getattr(logger, "_ag_initialized", False):
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False

    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path,
            maxBytes=512 * 1024,
            backupCount=2,
            encoding="utf-8",
            delay=True,
        )
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(handler)
    except OSError:
        # If we can't open the log file, fall back to a stderr handler so we
        # still capture diagnostics rather than silently dropping them.
        logger.addHandler(logging.StreamHandler())

    logger._ag_initialized = True  # type: ignore[attr-defined]
    return logger
