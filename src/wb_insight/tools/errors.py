"""Stable service-layer errors mapped to public HTTP responses."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class ToolServiceError(RuntimeError):
    """Base class for expected failures at the tool boundary."""

    code = "tool_error"

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.details = dict(details or {})


class CountryNotFoundError(ToolServiceError):
    """Raised when requested ISO3 codes are absent from the active run."""

    code = "country_not_found"


class NoActiveRunError(ToolServiceError):
    """Raised when ClickHouse contains no loaded analytical run."""

    code = "no_active_run"


class AnalyticsUnavailableError(ToolServiceError):
    """Raised when the analytical backend cannot serve a request."""

    code = "analytics_unavailable"
