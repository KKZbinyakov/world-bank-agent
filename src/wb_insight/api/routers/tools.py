"""Versioned analytical tool endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from wb_insight.analytics import (
    CorrelationResult,
    CountryComparisonResult,
    CountrySnapshotResult,
    DataQualityResult,
    TimeseriesResult,
    TrendResult,
)
from wb_insight.api.dependencies import get_tool_service
from wb_insight.api.responses import error_responses, success_response
from wb_insight.tools import (
    CompareCountriesToolRequest,
    CorrelationToolRequest,
    CountrySearchResult,
    DataQualityToolRequest,
    IndicatorSearchResult,
    SearchCountriesRequest,
    SearchIndicatorsRequest,
    SnapshotToolRequest,
    TimeseriesToolRequest,
    ToolResponse,
    ToolService,
    TrendToolRequest,
)

router = APIRouter(prefix="/v1/tools", tags=["tools"])
Service = Annotated[ToolService, Depends(get_tool_service)]


@router.post(
    "/search-countries",
    response_model=ToolResponse[CountrySearchResult],
    operation_id="search_countries_v1",
    responses=error_responses(),
)
def search_countries(
    payload: SearchCountriesRequest,
    request: Request,
    service: Service,
) -> ToolResponse[CountrySearchResult]:
    return success_response(
        request,
        tool="search_countries",
        data=service.search_countries(payload),
    )


@router.post(
    "/search-indicators",
    response_model=ToolResponse[IndicatorSearchResult],
    operation_id="search_indicators_v1",
    responses=error_responses(),
)
def search_indicators(
    payload: SearchIndicatorsRequest,
    request: Request,
    service: Service,
) -> ToolResponse[IndicatorSearchResult]:
    return success_response(
        request,
        tool="search_indicators",
        data=service.search_indicators(payload),
    )


@router.post(
    "/timeseries",
    response_model=ToolResponse[TimeseriesResult],
    operation_id="get_timeseries_v1",
    responses=error_responses(),
)
def get_timeseries(
    payload: TimeseriesToolRequest,
    request: Request,
    service: Service,
) -> ToolResponse[TimeseriesResult]:
    return success_response(
        request,
        tool="get_timeseries",
        data=service.get_timeseries(payload),
    )


@router.post(
    "/country-snapshot",
    response_model=ToolResponse[CountrySnapshotResult],
    operation_id="get_country_snapshot_v1",
    responses=error_responses(),
)
def get_country_snapshot(
    payload: SnapshotToolRequest,
    request: Request,
    service: Service,
) -> ToolResponse[CountrySnapshotResult]:
    return success_response(
        request,
        tool="get_country_snapshot",
        data=service.get_country_snapshot(payload),
    )


@router.post(
    "/trend",
    response_model=ToolResponse[TrendResult],
    operation_id="calculate_trend_v1",
    responses=error_responses(),
)
def trend(
    payload: TrendToolRequest,
    request: Request,
    service: Service,
) -> ToolResponse[TrendResult]:
    return success_response(
        request,
        tool="calculate_trend",
        data=service.calculate_trend(payload),
    )


@router.post(
    "/compare-countries",
    response_model=ToolResponse[CountryComparisonResult],
    operation_id="compare_countries_v1",
    responses=error_responses(),
)
def compare_countries(
    payload: CompareCountriesToolRequest,
    request: Request,
    service: Service,
) -> ToolResponse[CountryComparisonResult]:
    return success_response(
        request,
        tool="compare_countries",
        data=service.compare_countries(payload),
    )


@router.post(
    "/correlation",
    response_model=ToolResponse[CorrelationResult],
    operation_id="calculate_correlation_v1",
    responses=error_responses(),
)
def correlation(
    payload: CorrelationToolRequest,
    request: Request,
    service: Service,
) -> ToolResponse[CorrelationResult]:
    return success_response(
        request,
        tool="calculate_correlation",
        data=service.calculate_correlation(payload),
    )


@router.post(
    "/data-quality",
    response_model=ToolResponse[DataQualityResult],
    operation_id="get_data_quality_v1",
    responses=error_responses(),
)
def data_quality(
    payload: DataQualityToolRequest,
    request: Request,
    service: Service,
) -> ToolResponse[DataQualityResult]:
    return success_response(
        request,
        tool="get_data_quality",
        data=service.get_data_quality(payload),
    )
