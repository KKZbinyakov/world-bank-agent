"""Liveness and readiness endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from wb_insight import __version__
from wb_insight.api.dependencies import get_tool_service
from wb_insight.api.responses import error_responses, success_response
from wb_insight.tools import (
    AnalyticsUnavailableError,
    LiveStatus,
    ReadyStatus,
    ToolResponse,
    ToolService,
)

router = APIRouter(tags=["health"])


@router.get(
    "/health/live",
    response_model=ToolResponse[LiveStatus],
    operation_id="health_live",
)
def health_live(request: Request) -> ToolResponse[LiveStatus]:
    """Confirm that the API process is alive without querying ClickHouse."""

    return success_response(
        request,
        tool="health_live",
        data=LiveStatus(service="wb-insight-tool-api", version=__version__),
    )


@router.get(
    "/health/ready",
    response_model=ToolResponse[ReadyStatus],
    operation_id="health_ready",
    responses=error_responses(),
)
def health_ready(
    request: Request,
    service: Annotated[ToolService, Depends(get_tool_service)],
) -> ToolResponse[ReadyStatus]:
    """Confirm that analytical marts and a loaded run are available."""

    readiness = service.readiness()
    if not readiness.ready:
        raise AnalyticsUnavailableError(
            "The analytical backend is not ready.",
            details={
                "missing_objects": list(readiness.missing_objects),
                "current_run_id": readiness.current_run_id,
            },
        )
    return success_response(
        request,
        tool="health_ready",
        data=ReadyStatus(
            status="ready",
            ready=True,
            current_run_id=readiness.current_run_id,
            missing_objects=readiness.missing_objects,
        ),
    )
