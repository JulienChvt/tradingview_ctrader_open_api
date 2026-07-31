"""Structured (JSON lines) logging to a rotating file, plus console output."""
from __future__ import annotations

import json
import logging
import os
from logging.handlers import RotatingFileHandler

from config import settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update(extra)
        return json.dumps(payload)


def configure_logging() -> None:
    root = logging.getLogger()
    if root.handlers:
        return  # already configured

    root.setLevel(settings.log_level)

    os.makedirs(os.path.dirname(settings.log_file_path) or ".", exist_ok=True)

    file_handler = RotatingFileHandler(
        settings.log_file_path, maxBytes=10_000_000, backupCount=5
    )
    file_handler.setFormatter(JsonFormatter())
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(console_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
