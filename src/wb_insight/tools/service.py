"""Orchestration layer shared by HTTP, future MCP, and agent adapters."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

from wb_insight.analytics import (
    AnalyticalError,
    CorrelationResult,
    CountryCatalogEntry,
    CountryComparisonResult,
    CountrySnapshotResult,
    CurrentRunSummary,
    DataQualityResult,
    IndicatorCatalogEntry,
    MetricRequest,
    RepositoryReadiness,
    SnapshotMode,
    TimeseriesResult,
    TrendResult,
    calculate_correlation,
    calculate_trend,
    compare_countries,
)
from wb_insight.tools.errors import (
    AnalyticsUnavailableError,
    CountryNotFoundError,
    NoActiveRunError,
)
from wb_insight.tools.schemas import (
    CompareCountriesToolRequest,
    CorrelationToolRequest,
    CountrySearchItem,
    CountrySearchResult,
    DataQualityToolRequest,
    IndicatorSearchItem,
    IndicatorSearchResult,
    SearchCountriesRequest,
    SearchIndicatorsRequest,
    SnapshotToolRequest,
    TimeseriesToolRequest,
    TrendToolRequest,
)


class ToolRepository(Protocol):
    """Repository surface used by the tool service."""

    def get_readiness(self) -> RepositoryReadiness: ...

    def get_current_run(self) -> CurrentRunSummary | None: ...

    def get_countries(
        self,
        countries: Sequence[str],
    ) -> tuple[CountryCatalogEntry, ...]: ...

    def search_countries(
        self,
        *,
        query: str = "",
        region: str | None = None,
        income_level: str | None = None,
        additional_country_codes: Sequence[str] = (),
        limit: int = 20,
    ) -> tuple[CountryCatalogEntry, ...]: ...

    def search_indicators(
        self,
        *,
        query: str,
        categories: Sequence[str] = (),
        limit: int = 20,
    ) -> tuple[IndicatorCatalogEntry, ...]: ...

    def get_timeseries(
        self,
        *,
        countries: Sequence[str],
        metrics: Sequence[str | MetricRequest],
        start_year: int | None = None,
        end_year: int | None = None,
    ) -> TimeseriesResult: ...

    def get_country_snapshot(
        self,
        *,
        countries: Sequence[str],
        metrics: Sequence[str | MetricRequest],
        mode: SnapshotMode = "latest_available",
        year: int | None = None,
    ) -> CountrySnapshotResult: ...

    def get_data_quality(
        self,
        *,
        countries: Sequence[str],
        metrics: Sequence[str | MetricRequest],
    ) -> DataQualityResult: ...


class ToolService:
    """Compose bounded repository queries with deterministic calculations."""

    def __init__(
        self,
        repository: ToolRepository,
        *,
        country_labels: Mapping[str, str] | None = None,
    ) -> None:
        self._repository = repository
        self._country_labels = {
            str(code).strip().upper(): str(label) for code, label in (country_labels or {}).items()
        }

    def readiness(self) -> RepositoryReadiness:
        return self._backend_call(self._repository.get_readiness)

    def current_run(self) -> CurrentRunSummary:
        result = self._backend_call(self._repository.get_current_run)
        if result is None:
            raise NoActiveRunError("No loaded analytical run is available.")
        return result

    def search_countries(self, request: SearchCountriesRequest) -> CountrySearchResult:
        query_folded = request.query.casefold()
        label_codes = tuple(
            code
            for code, label in self._country_labels.items()
            if query_folded and query_folded in label.casefold()
        )
        matches = self._backend_call(
            self._repository.search_countries,
            query=request.query,
            region=request.region,
            income_level=request.income_level,
            additional_country_codes=label_codes,
            limit=request.limit,
        )
        return CountrySearchResult(
            query=request.query,
            matches=tuple(
                CountrySearchItem(
                    country_code=item.country_code,
                    country_name=item.country_name,
                    country_name_ru=self._country_labels.get(item.country_code),
                    region_name=item.region_name,
                    income_level_name=item.income_level_name,
                    longitude=item.longitude,
                    latitude=item.latitude,
                )
                for item in matches
            ),
        )

    def search_indicators(self, request: SearchIndicatorsRequest) -> IndicatorSearchResult:
        matches = self._backend_call(
            self._repository.search_indicators,
            query=request.query,
            categories=request.categories,
            limit=request.limit,
        )
        return IndicatorSearchResult(
            query=request.query,
            matches=tuple(
                IndicatorSearchItem(
                    source_id=item.source_id,
                    indicator_code=item.indicator_code,
                    metric_key=item.metric_key,
                    alias=item.alias,
                    indicator_name=item.indicator_name,
                    indicator_name_ru=item.indicator_name_ru,
                    category=item.category,
                    unit=item.unit,
                    display_unit=item.display_unit,
                    dimensions_json=item.dimensions_json,
                )
                for item in matches
            ),
        )

    def get_timeseries(self, request: TimeseriesToolRequest) -> TimeseriesResult:
        self._ensure_countries(request.countries)
        return self._backend_call(
            self._repository.get_timeseries,
            countries=request.countries,
            metrics=request.metrics,
            start_year=request.start_year,
            end_year=request.end_year,
        )

    def get_country_snapshot(self, request: SnapshotToolRequest) -> CountrySnapshotResult:
        self._ensure_countries(request.countries)
        return self._backend_call(
            self._repository.get_country_snapshot,
            countries=request.countries,
            metrics=request.metrics,
            mode=request.mode,
            year=request.year,
        )

    def calculate_trend(self, request: TrendToolRequest) -> TrendResult:
        country = request.country.strip().upper()
        self._ensure_countries((country,))
        series = self._backend_call(
            self._repository.get_timeseries,
            countries=(country,),
            metrics=(request.metric,),
            start_year=request.start_year,
            end_year=request.end_year,
        )
        return calculate_trend(series.points)

    def compare_countries(
        self,
        request: CompareCountriesToolRequest,
    ) -> CountryComparisonResult:
        self._ensure_countries(request.countries)
        series = self._backend_call(
            self._repository.get_timeseries,
            countries=request.countries,
            metrics=(request.metric,),
            start_year=request.year,
            end_year=request.year,
        )
        return compare_countries(
            series.points,
            year=request.year,
            descending=request.descending,
        )

    def calculate_correlation(self, request: CorrelationToolRequest) -> CorrelationResult:
        self._ensure_countries(request.countries)
        x_series = self._backend_call(
            self._repository.get_timeseries,
            countries=request.countries,
            metrics=(request.x_metric,),
            start_year=request.start_year,
            end_year=request.end_year,
        )
        y_series = self._backend_call(
            self._repository.get_timeseries,
            countries=request.countries,
            metrics=(request.y_metric,),
            start_year=request.start_year,
            end_year=request.end_year,
        )
        return calculate_correlation(
            x_series.points,
            y_series.points,
            method=request.method,
            min_observations=request.min_observations,
        )

    def get_data_quality(self, request: DataQualityToolRequest) -> DataQualityResult:
        self._ensure_countries(request.countries)
        return self._backend_call(
            self._repository.get_data_quality,
            countries=request.countries,
            metrics=request.metrics,
        )

    def _ensure_countries(self, countries: Sequence[str]) -> None:
        normalized = tuple(dict.fromkeys(str(code).strip().upper() for code in countries))
        available = self._backend_call(self._repository.get_countries, normalized)
        found = {item.country_code for item in available}
        missing = tuple(code for code in normalized if code not in found)
        if missing:
            raise CountryNotFoundError(
                f"Countries are absent from the active analytical run: {', '.join(missing)}.",
                details={"countries": list(missing)},
            )

    @staticmethod
    def _backend_call[**Params, ResultT](
        function: Callable[Params, ResultT],
        /,
        *args: Params.args,
        **kwargs: Params.kwargs,
    ) -> ResultT:
        try:
            return function(*args, **kwargs)
        except (AnalyticalError, ValueError):
            raise
        except Exception as exc:
            raise AnalyticsUnavailableError(
                "The analytical backend is temporarily unavailable."
            ) from exc
