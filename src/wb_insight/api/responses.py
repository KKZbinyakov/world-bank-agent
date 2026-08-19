"""Helpers for stable success and error response contracts."""

from __future__ import annotations

from time import perf_counter
from typing import Any

from fastapi import Request

from wb_insight.tools import ErrorResponse, ToolResponse


def request_id(request: Request) -> str:
    """Return the request id established by middleware."""

    return str(getattr(request.state, "request_id", "unknown"))


def elapsed_ms(request: Request) -> float:
    """Return elapsed request time rounded for stable JSON output."""

    started_at = float(getattr(request.state, "started_at", perf_counter()))
    return round(max((perf_counter() - started_at) * 1000, 0.0), 3)


def success_response[DataT](
    request: Request,
    *,
    tool: str,
    data: DataT,
) -> ToolResponse[DataT]:
    """Wrap tool output in the public success envelope."""

    return ToolResponse(
        request_id=request_id(request),
        tool=tool,
        elapsed_ms=elapsed_ms(request),
        data=data,
    )


def error_responses() -> dict[int | str, dict[str, Any]]:
    """OpenAPI declarations shared by analytical endpoints."""

    return {
        404: {"model": ErrorResponse, "description": "Requested resource was not found."},
        409: {"model": ErrorResponse, "description": "Request is ambiguous or incomplete."},
        422: {"model": ErrorResponse, "description": "Request validation or safety limit failed."},
        503: {"model": ErrorResponse, "description": "Analytical backend is unavailable."},
        500: {"model": ErrorResponse, "description": "Unexpected internal error."},
    }
