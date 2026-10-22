"""
Structured, rotating logging system for Game Save Guardian.
Keeps logs in the user application data directory and supports operation IDs.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import shutil
import zipfile
from typing import Optional

from src.utils.paths import get_logs_dir

LOGGER_NAME = "GameSaveGuardian"
_logger: Optional[logging.Logger] = None


class OperationIdFilter(logging.Filter):
    """Inject an operation_id into log records if missing."""
    def __init__(self, operation_id: str = "GLOBAL"):
        super().__init__()
        self.operation_id = operation_id

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "operation_id"):
            record.operation_id = self.operation_id
        return True


def setup_logging(verbose: bool = False) -> logging.Logger:
    """Initialize rotating file logger and console handler."""
    global _logger
    if _logger is not None:
        return _logger

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False

    # Clear existing handlers if any
    for h in list(logger.handlers):
        logger.removeHandler(h)

    logs_dir = get_logs_dir()
    log_file = logs_dir / "app.log"

    # 5 MB max per file, 5 backups = 25 MB max storage
    file_handler = RotatingFileHandler(
        str(log_file),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG if verbose else logging.INFO)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [OP:%(operation_id)s] [%(name)s.%(module)s]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    op_filter = OperationIdFilter()
    file_handler.addFilter(op_filter)
    console_handler.addFilter(op_filter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    _logger = logger
    logger.info("Logging initialized at %s", log_file, extra={"operation_id": "INIT"})
    return logger


def get_logger() -> logging.Logger:
    """Return the global logger instance, initializing if needed."""
    global _logger
    if _logger is None:
        return setup_logging()
    return _logger


def export_diagnostic_logs(target_zip_path: Path | str) -> Path:
    """Export all log files to a single ZIP archive for troubleshooting."""
    logs_dir = get_logs_dir()
    out_path = Path(target_zip_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(str(out_path), "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for log_file in logs_dir.glob("*.log*"):
            if log_file.is_file():
                zf.write(str(log_file), arcname=log_file.name)

    return out_path
