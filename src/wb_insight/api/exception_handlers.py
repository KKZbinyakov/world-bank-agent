"""Map internal exceptions to stable public error envelopes."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from wb_insight.analytics import (
    AmbiguousMetricError,
    AnalyticalError,
    DimensionRequiredError,
    MetricNotFoundError,
    ResultLimitError,
)
from wb_insight.api.responses import request_id
from wb_insight.tools import (
    AnalyticsUnavailableError,
    CountryNotFoundError,
    ErrorDetail,
    ErrorResponse,
    NoActiveRunError,
    ToolServiceError,
)

logger = logging.getLogger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    """Install all public exception mappings on an application."""

    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(MetricNotFoundError, _metric_not_found)
    app.add_exception_handler(AmbiguousMetricError, _ambiguous_metric)
    app.add_exception_handler(DimensionRequiredError, _dimension_required)
    app.add_exception_handler(ResultLimitError, _result_limit)
    app.add_exception_handler(CountryNotFoundError, _country_not_found)
    app.add_exception_handler(NoActiveRunError, _no_active_run)
    app.add_exception_handler(AnalyticsUnavailableError, _analytics_unavailable)
    app.add_exception_handler(ToolServiceError, _tool_service_error)
    app.add_exception_handler(AnalyticalError, _analytical_error)
    app.add_exception_handler(ValueError, _invalid_request)
    app.add_exception_handler(Exception, _internal_error)


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    validation = exc if isinstance(exc, RequestValidationError) else None
    details = {
        "errors": [
            {
                "location": list(item.get("loc", ())),
                "message": str(item.get("msg", "Invalid value")),
                "type": str(item.get("type", "validation_error")),
            }
            for item in (validation.errors() if validation else [])
        ]
    }
    return _error_response(
        request,
        status_code=422,
        code="validation_error",
        message="Request validation failed.",
        details=details,
    )


async def _metric_not_found(request: Request, exc: Exception) -> JSONResponse:
    return _error_response(request, 404, "metric_not_found", str(exc))


async def _ambiguous_metric(request: Request, exc: Exception) -> JSONResponse:
    return _error_response(request, 409, "ambiguous_metric", str(exc))


async def _dimension_required(request: Request, exc: Exception) -> JSONResponse:
    return _error_response(request, 409, "dimension_required", str(exc))


async def _result_limit(request: Request, exc: Exception) -> JSONResponse:
    return _error_response(request, 422, "result_limit_exceeded", str(exc))


async def _country_not_found(request: Request, exc: Exception) -> JSONResponse:
    details = exc.details if isinstance(exc, CountryNotFoundError) else {}
    return _error_response(request, 404, "country_not_found", str(exc), details)


async def _no_active_run(request: Request, exc: Exception) -> JSONResponse:
    return _error_response(request, 404, "no_active_run", str(exc))


async def _analytics_unavailable(request: Request, exc: Exception) -> JSONResponse:
    details = exc.details if isinstance(exc, AnalyticsUnavailableError) else {}
    return _error_response(
        request,
        503,
        "analytics_unavailable",
        str(exc),
        details,
    )


async def _tool_service_error(request: Request, exc: Exception) -> JSONResponse:
    service_error = exc if isinstance(exc, ToolServiceError) else None
    return _error_response(
        request,
        422,
        service_error.code if service_error else "tool_error",
        str(exc),
        service_error.details if service_error else {},
    )


async def _analytical_error(request: Request, exc: Exception) -> JSONResponse:
    return _error_response(request, 422, "analytical_error", str(exc))


async def _invalid_request(request: Request, exc: Exception) -> JSONResponse:
    return _error_response(request, 422, "invalid_request", str(exc))


async def _internal_error(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "Unhandled Tool API error",
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return _error_response(
        request,
        500,
        "internal_error",
        "An unexpected internal error occurred.",
    )


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: Mapping[str, Any] | None = None,
) -> JSONResponse:
    payload = ErrorResponse(
        error=ErrorDetail(
            code=code,
            message=message,
            request_id=request_id(request),
            details=dict(details or {}),
        )
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))
