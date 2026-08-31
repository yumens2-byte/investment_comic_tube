"""Application logging shared by local runs and GitHub Actions."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import time


def configure_logging(log_dir: str | Path | None = None) -> Path:
    """Configure console and rotating file logs, returning the main log path."""
    directory = Path(log_dir or os.getenv("LOG_DIR", "logs"))
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / "pipeline.log"

    formatter = logging.Formatter(
        "%(asctime)sZ %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    formatter.converter = time.gmtime

    root = logging.getLogger()
    root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=int(os.getenv("LOG_MAX_BYTES", "5242880")),
        backupCount=int(os.getenv("LOG_BACKUP_COUNT", "3")),
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(console)
    root.addHandler(file_handler)
    return log_path
