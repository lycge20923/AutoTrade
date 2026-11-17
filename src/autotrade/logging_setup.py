from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "autotrade"
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(level: str = "INFO", log_file: Path | None = None) -> logging.Logger:
    """設定 autotrade logger：輸出到終端機，並（選擇性）寫入檔案，每檔 1MB、保留 5 份。"""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter(_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file, maxBytes=1_000_000, backupCount=5, encoding="utf-8", delay=True
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger
