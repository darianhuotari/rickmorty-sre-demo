import os
import logging
import logging.config
from pathlib import Path
from typing import Dict, Any


_configured = False  # idempotency guard


def _build_dict_config(log_file: str | None, level: str) -> Dict[str, Any]:
    handlers: Dict[str, Any] = {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "std",
        }
    }

    root_handlers = ["console"]

    if log_file:
        # Ensure directory exists and use a file handler that plays nice with tail/rotate
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers["file"] = {
            "class": "logging.handlers.WatchedFileHandler",
            "filename": log_file,
            "formatter": "std",
        }
        root_handlers.append("file")

    return {
        "version": 1,
        "disable_existing_loggers": False,  # keep library loggers
        "formatters": {
            "std": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}
        },
        "handlers": handlers,
        "root": {"level": level, "handlers": root_handlers},
    }


def configure_logging() -> None:
    """Configure logging to stdout and (optionally) to LOG_FILE_PATH.

    Idempotent: safe to call multiple times (e.g., per Uvicorn worker import).
    """
    global _configured
    if _configured:
        return

    level = os.getenv("LOG_LEVEL", "INFO").upper()
    log_file = os.getenv("LOG_FILE_PATH") or None

    cfg = _build_dict_config(log_file, level)
    logging.config.dictConfig(cfg)

    # Align common loggers with the root level
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        logging.getLogger(name).setLevel(level)

    _configured = True
