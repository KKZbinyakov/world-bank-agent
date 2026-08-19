"""HTTP-neutral request and response contracts for analytical tools."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from wb_insight.analytics import (
    CorrelationMethod,
    MetricRequest,
    SnapshotMode,
)

MetricSelector = str | MetricRequest


class ToolModel(BaseModel):
    """Strict base model for the public tool boundary."""

    model_config = ConfigDict(extra="forbid")


class ToolResponse[DataT](ToolModel):
    """Successful API envelope shared by every endpoint."""

    request_id: str
    tool: str
    elapsed_ms: float = Field(ge=0)
    data: DataT


class ErrorDetail(ToolModel):
    """Stable error body that never exposes storage internals."""

    code: str
    message: str
    request_id: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(ToolModel):
    """Public error envelope."""

    error: ErrorDetail


class LiveStatus(ToolModel):
    """Liveness response independent from ClickHouse."""

    status: str = "ok"
    service: str
    version: str


class ReadyStatus(ToolModel):
    """Readiness response backed by the analytical repository."""

    status: str
    ready: bool
    current_run_id: str | None = None
    missing_objects: tuple[str, ...] = ()


class SearchCountriesRequest(ToolModel):
    """Country catalog search constrained to the active analytical run."""

    query: str = Field(default="", max_length=200)
    region: str | None = Field(default=None, max_length=200)
    income_level: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("query", "region", "income_level")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


class CountrySearchItem(ToolModel):
    """One country candidate for a later analytical request."""

    country_code: str
    country_name: str
    country_name_ru: str | None = None
    region_name: str | None = None
    income_level_name: str | None = None
    longitude: float | None = None
    latitude: float | None = None


class CountrySearchResult(ToolModel):
    """Country search result."""

    query: str
    matches: tuple[CountrySearchItem, ...]


class SearchIndicatorsRequest(ToolModel):
    """Indicator search constrained to metrics loaded in the active run."""

    query: str = Field(min_length=1, max_length=200)
    categories: tuple[str, ...] = Field(default=(), max_length=20)
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("indicator search query cannot be blank")
        return normalized

    @field_validator("categories")
    @classmethod
    def normalize_categories(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(
            dict.fromkeys(value.strip().lower() for value in values if value.strip())
        )
        return normalized


class IndicatorSearchItem(ToolModel):
    """One source-qualified indicator available to analytical tools."""

    source_id: int
    indicator_code: str
    metric_key: str
    alias: str | None = None
    indicator_name: str
    indicator_name_ru: str | None = None
    category: str | None = None
    unit: str | None = None
    display_unit: str | None = None
    dimensions_json: tuple[str, ...] = ()


class IndicatorSearchResult(ToolModel):
    """Indicator search result."""

    query: str
    matches: tuple[IndicatorSearchItem, ...]


class TimeseriesToolRequest(ToolModel):
    """Request time series for one or more countries and metrics."""

    countries: tuple[str, ...] = Field(min_length=1, max_length=50)
    metrics: tuple[MetricSelector, ...] = Field(min_length=1, max_length=20)
    start_year: int | None = Field(default=None, ge=1800, le=2200)
    end_year: int | None = Field(default=None, ge=1800, le=2200)

    @model_validator(mode="after")
    def validate_period(self) -> TimeseriesToolRequest:
        if (
            self.start_year is not None
            and self.end_year is not None
            and self.end_year < self.start_year
        ):
            raise ValueError("end_year must be greater than or equal to start_year")
        return self


class SnapshotToolRequest(ToolModel):
    """Request latest, common-year, or explicit-year values."""

    countries: tuple[str, ...] = Field(min_length=1, max_length=50)
    metrics: tuple[MetricSelector, ...] = Field(min_length=1, max_length=20)
    mode: SnapshotMode = "common_year"
    year: int | None = Field(default=None, ge=1800, le=2200)

    @model_validator(mode="after")
    def validate_mode_and_year(self) -> SnapshotToolRequest:
        if self.mode == "year" and self.year is None:
            raise ValueError("year is required when mode='year'")
        if self.mode != "year" and self.year is not None:
            raise ValueError("year can only be supplied when mode='year'")
        return self


class TrendToolRequest(ToolModel):
    """Calculate trend statistics for exactly one country/metric series."""

    country: str = Field(min_length=3, max_length=3)
    metric: MetricSelector
    start_year: int | None = Field(default=None, ge=1800, le=2200)
    end_year: int | None = Field(default=None, ge=1800, le=2200)

    @model_validator(mode="after")
    def validate_period(self) -> TrendToolRequest:
        if (
            self.start_year is not None
            and self.end_year is not None
            and self.end_year < self.start_year
        ):
            raise ValueError("end_year must be greater than or equal to start_year")
        return self


class CompareCountriesToolRequest(ToolModel):
    """Compare countries by one metric at an explicit or latest common year."""

    countries: tuple[str, ...] = Field(min_length=2, max_length=50)
    metric: MetricSelector
    year: int | None = Field(default=None, ge=1800, le=2200)
    descending: bool = True


class CorrelationToolRequest(ToolModel):
    """Correlate two metrics over matched country/year observations."""

    countries: tuple[str, ...] = Field(min_length=1, max_length=50)
    x_metric: MetricSelector
    y_metric: MetricSelector
    start_year: int | None = Field(default=None, ge=1800, le=2200)
    end_year: int | None = Field(default=None, ge=1800, le=2200)
    method: CorrelationMethod = "pearson"
    min_observations: int = Field(default=3, ge=3, le=50_000)

    @model_validator(mode="after")
    def validate_period(self) -> CorrelationToolRequest:
        if (
            self.start_year is not None
            and self.end_year is not None
            and self.end_year < self.start_year
        ):
            raise ValueError("end_year must be greater than or equal to start_year")
        return self


class DataQualityToolRequest(ToolModel):
    """Request coverage metadata for country/metric series."""

    countries: tuple[str, ...] = Field(min_length=1, max_length=50)
    metrics: tuple[MetricSelector, ...] = Field(min_length=1, max_length=20)
