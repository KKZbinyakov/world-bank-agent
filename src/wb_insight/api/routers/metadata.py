"""Metadata endpoints for the active analytical run."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from wb_insight.analytics import CurrentRunSummary
from wb_insight.api.dependencies import get_tool_service
from wb_insight.api.responses import error_responses, success_response
from wb_insight.tools import ToolResponse, ToolService

router = APIRouter(prefix="/v1/meta", tags=["metadata"])


@router.get(
    "/current-run",
    response_model=ToolResponse[CurrentRunSummary],
    operation_id="get_current_run_v1",
    responses=error_responses(),
)
def get_current_run(
    request: Request,
    service: Annotated[ToolService, Depends(get_tool_service)],
) -> ToolResponse[CurrentRunSummary]:
    """Return the active run and its actual analytical scope."""

    return success_response(
        request,
        tool="get_current_run",
        data=service.current_run(),
    )
