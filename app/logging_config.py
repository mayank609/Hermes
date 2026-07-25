"""
Application-wide logging configuration.

Logs go to both the console (human-friendly, for `docker logs` / terminal
use) and a rotating file under data/logs/agent.log (for post-mortem
debugging in production). Call `setup_logging()` once, at process startup,
before any other module logs anything.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per log file
_BACKUP_COUNT = 5


def setup_logging(log_level: str, log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "agent.log"

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        log_file, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(log_level.upper())
    # Avoid duplicate handlers if setup_logging is called more than once
    # (e.g. in tests that import main multiple times).
    root.handlers.clear()
    root.addHandler(console_handler)
    root.addHandler(file_handler)

    # Third-party libraries are noisy at INFO/DEBUG; keep them at WARNING
    # unless the whole app is explicitly running in DEBUG mode.
    if log_level.upper() != "DEBUG":
        logging.getLogger("telethon").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("google_genai").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
