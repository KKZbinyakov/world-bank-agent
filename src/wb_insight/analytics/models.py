"""Typed contracts shared by the analytical repository and pure calculations."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CorrelationMethod = Literal["pearson", "spearman"]
SnapshotMode = Literal["latest_available", "common_year", "year"]


class AnalyticalError(RuntimeError):
    """Base error for invalid analytical requests or ambiguous data."""


class MetricNotFoundError(AnalyticalError):
    """Raised when a metric selector is absent from the active analytical run."""


class AmbiguousMetricError(AnalyticalError):
    """Raised when an indicator code resolves to more than one source."""


class ResultLimitError(AnalyticalError):
    """Raised when a query would return more rows than the configured safety limit."""


class DimensionRequiredError(AnalyticalError):
    """Raised when a multidimensional metric requires an explicit slice."""


class FrozenModel(BaseModel):
    """Strict immutable base model used by the analytical core."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class MetricRequest(FrozenModel):
    """User-facing metric selector with an exact optional dimension slice."""

    selector: str = Field(min_length=1, max_length=200)
    dimensions: dict[str, str] = Field(default_factory=dict)

    @field_validator("selector")
    @classmethod
    def normalize_selector(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("metric selector cannot be blank")
        return normalized

    @field_validator("dimensions")
    @classmethod
    def normalize_dimensions(cls, value: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, item in value.items():
            clean_key = str(key).strip()
            clean_value = str(item).strip()
            if not clean_key or not clean_value:
                raise ValueError("dimension names and values cannot be blank")
            normalized[clean_key] = clean_value
        return dict(sorted(normalized.items()))

    @property
    def dimensions_json(self) -> str:
        """Return the canonical representation used in ClickHouse keys."""

        return json.dumps(
            self.dimensions,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


class ResolvedMetric(FrozenModel):
    """Unambiguous metric identity and semantic metadata from the active run."""

    run_id: str
    source_id: int
    indicator_code: str
    alias: str | None = None
    indicator_name: str
    indicator_name_ru: str | None = None
    category: str | None = None
    unit: str | None = None
    display_unit: str | None = None
    dimensions_json: str = "{}"

    @property
    def metric_key(self) -> str:
        return f"{self.source_id}:{self.indicator_code}"


class TimeseriesPoint(FrozenModel):
    """One citable country/indicator/year observation."""

    run_id: str
    source_id: int
    indicator_code: str
    indicator_alias: str | None = None
    indicator_name: str
    indicator_name_ru: str | None = None
    indicator_category: str | None = None
    unit: str | None = None
    display_unit: str | None = None
    country_code: str
    country_name: str
    region_name: str | None = None
    income_level_name: str | None = None
    year: int
    value: float | None
    dimensions_json: str = "{}"
    is_missing: bool = False

    @property
    def metric_key(self) -> str:
        return f"{self.source_id}:{self.indicator_code}"

    @property
    def series_key(self) -> tuple[str, int, str, str]:
        return (
            self.country_code,
            self.source_id,
            self.indicator_code,
            self.dimensions_json,
        )


class SeriesCoverage(FrozenModel):
    """Completeness metadata for one country/metric/dimension series."""

    country_code: str
    source_id: int
    indicator_code: str
    dimensions_json: str
    expected_years: int
    row_count: int
    non_null_count: int
    coverage_ratio: float = Field(ge=0, le=1)
    missing_years: tuple[int, ...]


class TimeseriesResult(FrozenModel):
    """Result of a bounded time-series query."""

    run_id: str | None
    countries: tuple[str, ...]
    metrics: tuple[ResolvedMetric, ...]
    start_year: int | None
    end_year: int | None
    points: tuple[TimeseriesPoint, ...]
    coverage: tuple[SeriesCoverage, ...]
    warnings: tuple[str, ...] = ()


class SnapshotPoint(FrozenModel):
    """One value used in a country snapshot."""

    run_id: str
    source_id: int
    indicator_code: str
    indicator_alias: str | None = None
    indicator_name: str
    indicator_name_ru: str | None = None
    indicator_category: str | None = None
    unit: str | None = None
    display_unit: str | None = None
    country_code: str
    country_name: str
    region_name: str | None = None
    income_level_name: str | None = None
    observation_year: int | None
    value: float | None
    dimensions_json: str = "{}"

    @property
    def metric_key(self) -> str:
        return f"{self.source_id}:{self.indicator_code}"


class CountrySnapshotResult(FrozenModel):
    """Latest, common-year, or fixed-year country snapshot."""

    mode: SnapshotMode
    comparison_year: int | None
    countries: tuple[str, ...]
    metrics: tuple[ResolvedMetric, ...]
    points: tuple[SnapshotPoint, ...]
    missing_pairs: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class TrendResult(FrozenModel):
    """Deterministic trend statistics for exactly one time series."""

    run_id: str | None
    country_code: str
    source_id: int
    indicator_code: str
    indicator_alias: str | None = None
    unit: str | None = None
    display_unit: str | None = None
    dimensions_json: str = "{}"
    start_year: int | None = None
    end_year: int | None = None
    start_value: float | None = None
    end_value: float | None = None
    observation_count: int
    missing_count: int
    absolute_change: float | None = None
    percent_change: float | None = None
    cagr_percent: float | None = None
    average_annual_change: float | None = None
    linear_slope_per_year: float | None = None
    annualized_change_volatility_percent: float | None = None
    evidence: tuple[TimeseriesPoint, ...] = ()
    warnings: tuple[str, ...] = ()


class CountryComparisonEntry(FrozenModel):
    """One country row in a value-based comparison."""

    country_code: str
    country_name: str
    year: int
    value: float
    rank: int
    difference_from_mean: float
    difference_from_median: float
    percent_difference_from_median: float | None


class CountryComparisonResult(FrozenModel):
    """Cross-country comparison for one metric and one comparable year."""

    run_id: str | None
    source_id: int
    indicator_code: str
    indicator_alias: str | None = None
    unit: str | None = None
    display_unit: str | None = None
    dimensions_json: str = "{}"
    year: int | None
    descending: bool
    mean: float | None
    median: float | None
    entries: tuple[CountryComparisonEntry, ...]
    evidence: tuple[TimeseriesPoint, ...] = ()
    missing_countries: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class CorrelationPair(FrozenModel):
    """One matched evidence pair used by a correlation calculation."""

    country_code: str
    year: int
    x_value: float
    y_value: float


class CorrelationResult(FrozenModel):
    """Correlation computed over matched country/year evidence pairs."""

    method: CorrelationMethod
    x_source_id: int
    x_indicator_code: str
    x_indicator_alias: str | None = None
    x_unit: str | None = None
    x_display_unit: str | None = None
    x_dimensions_json: str = "{}"
    y_source_id: int
    y_indicator_code: str
    y_indicator_alias: str | None = None
    y_unit: str | None = None
    y_display_unit: str | None = None
    y_dimensions_json: str = "{}"
    coefficient: float | None
    sample_size: int
    dropped_pairs: int
    countries_used: tuple[str, ...]
    years_used: tuple[int, ...]
    run_id: str | None = None
    pairs: tuple[CorrelationPair, ...] = ()
    warnings: tuple[str, ...] = ()


class DataQualityEntry(FrozenModel):
    """Coverage statistics from the ClickHouse data-quality mart."""

    run_id: str
    source_id: int
    indicator_code: str
    indicator_alias: str | None = None
    indicator_name: str
    indicator_category: str | None = None
    country_code: str
    country_name: str
    dimensions_json: str = "{}"
    row_count: int
    non_null_count: int
    null_count: int
    expected_years: int
    coverage_ratio: float = Field(ge=0, le=1)
    first_available_year: int | None
    latest_available_year: int | None


class DataQualityResult(FrozenModel):
    """Bounded data-quality response for requested countries and metrics."""

    countries: tuple[str, ...]
    metrics: tuple[ResolvedMetric, ...]
    entries: tuple[DataQualityEntry, ...]
    warnings: tuple[str, ...] = ()


class CurrentRunSummary(FrozenModel):
    """Metadata and active analytical scope of the latest loaded run."""

    run_id: str
    loaded_at: datetime
    country_count: int = Field(ge=0)
    indicator_count: int = Field(ge=0)
    observation_count: int = Field(ge=0)
    start_year: int | None = None
    end_year: int | None = None
    source_ids: tuple[int, ...] = ()


class RepositoryReadiness(FrozenModel):
    """Readiness state of the ClickHouse analytical boundary."""

    ready: bool
    current_run_id: str | None = None
    missing_objects: tuple[str, ...] = ()


class CountryCatalogEntry(FrozenModel):
    """One country available in the active analytical run."""

    run_id: str
    country_code: str
    country_name: str
    region_name: str | None = None
    income_level_name: str | None = None
    longitude: float | None = None
    latitude: float | None = None


class IndicatorCatalogEntry(FrozenModel):
    """One source-qualified indicator available in the active analytical run."""

    run_id: str
    source_id: int
    indicator_code: str
    alias: str | None = None
    indicator_name: str
    indicator_name_ru: str | None = None
    category: str | None = None
    unit: str | None = None
    display_unit: str | None = None
    dimensions_json: tuple[str, ...] = ()

    @property
    def metric_key(self) -> str:
        return f"{self.source_id}:{self.indicator_code}"
