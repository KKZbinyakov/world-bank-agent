"""Logging configuration for local commands and future services."""

from __future__ import annotations

import logging


def configure_logging(level: str = "INFO") -> None:
    """Configure a consistent application log format.

    Args:
        level: Standard Python logging level name.
    """

    normalized_level = level.upper()
    numeric_level = getattr(logging, normalized_level, logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        force=True,
    )
