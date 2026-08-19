"""FastAPI dependencies for application settings and request-scoped tools."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Annotated, cast

from fastapi import Depends, Request

from wb_insight.analytics import AnalyticalRepository
from wb_insight.config import AppSettings
from wb_insight.tools import AnalyticsUnavailableError, ToolService


def get_app_settings(request: Request) -> AppSettings:
    """Return settings frozen on the application instance."""

    return cast(AppSettings, request.app.state.settings)


def get_country_labels(request: Request) -> Mapping[str, str]:
    """Return optional presentation labels loaded at application startup."""

    return cast(Mapping[str, str], request.app.state.country_labels)


def get_tool_service(
    settings: Annotated[AppSettings, Depends(get_app_settings)],
    country_labels: Annotated[Mapping[str, str], Depends(get_country_labels)],
) -> Iterator[ToolService]:
    """Provide a request-scoped read-only analytical service."""

    try:
        repository = AnalyticalRepository.from_settings(settings)
    except Exception as exc:
        raise AnalyticsUnavailableError("The analytical backend could not be initialized.") from exc

    try:
        yield ToolService(repository, country_labels=country_labels)
    finally:
        repository.close()
