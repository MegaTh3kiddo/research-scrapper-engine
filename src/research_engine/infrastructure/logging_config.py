"""Bounded local application logging without source payloads or credentials."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_local_logging(data_directory: Path) -> Path:
    path = data_directory / "research-engine.log"
    logger = logging.getLogger("research_engine")
    logger.setLevel(logging.INFO)
    # Alembic's logging fileConfig may disable pre-existing application loggers.
    logger.disabled = False
    for name, child in logging.root.manager.loggerDict.items():
        if name.startswith("research_engine.") and isinstance(child, logging.Logger):
            child.disabled = False
    for handler in tuple(logger.handlers):
        if getattr(handler, "_research_engine_managed", False):
            logger.removeHandler(handler)
            handler.close()
    handler = RotatingFileHandler(
        path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"
    ))
    handler._research_engine_managed = True
    logger.addHandler(handler)
    logger.propagate = False
    return path
