from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from wb_insight.analytics import (
    CountryCatalogEntry,
    CountrySnapshotResult,
    CurrentRunSummary,
    DataQualityEntry,
    DataQualityResult,
    IndicatorCatalogEntry,
    RepositoryReadiness,
    ResolvedMetric,
    SeriesCoverage,
    SnapshotPoint,
    TimeseriesPoint,
    TimeseriesResult,
)
from wb_insight.tools import (
    CompareCountriesToolRequest,
    CorrelationToolRequest,
    CountryNotFoundError,
    DataQualityToolRequest,
    SearchCountriesRequest,
    SearchIndicatorsRequest,
    SnapshotToolRequest,
    TimeseriesToolRequest,
    ToolService,
    TrendToolRequest,
)


def _metric(code: str = "NY.GDP.PCAP.CD", alias: str = "gdp_per_capita") -> ResolvedMetric:
    return ResolvedMetric(
        run_id="run-1",
        source_id=2,
        indicator_code=code,
        alias=alias,
        indicator_name=code,
        category="economy",
        unit="unit",
        display_unit="display",
    )


def _point(
    country: str,
    year: int,
    value: float,
    *,
    code: str = "NY.GDP.PCAP.CD",
    alias: str = "gdp_per_capita",
) -> TimeseriesPoint:
    names = {"DEU": "Germany", "FRA": "France", "POL": "Poland"}
    return TimeseriesPoint(
        run_id="run-1",
        source_id=2,
        indicator_code=code,
        indicator_alias=alias,
        indicator_name=code,
        indicator_category="economy",
        unit="unit",
        display_unit="display",
        country_code=country,
        country_name=names[country],
        region_name="Europe & Central Asia",
        income_level_name="High income",
        year=year,
        value=value,
    )


class FakeToolRepository:
    def __init__(self) -> None:
        self.countries = {
            "DEU": CountryCatalogEntry(
                run_id="run-1",
                country_code="DEU",
                country_name="Germany",
                region_name="Europe & Central Asia",
                income_level_name="High income",
                longitude=13.4,
                latitude=52.5,
            ),
            "FRA": CountryCatalogEntry(
                run_id="run-1",
                country_code="FRA",
                country_name="France",
                region_name="Europe & Central Asia",
                income_level_name="High income",
                longitude=2.3,
                latitude=48.9,
            ),
            "POL": CountryCatalogEntry(
                run_id="run-1",
                country_code="POL",
                country_name="Poland",
                region_name="Europe & Central Asia",
                income_level_name="High income",
                longitude=21.0,
                latitude=52.2,
            ),
        }
        self.metrics = {
            "gdp_per_capita": _metric(),
            "unemployment": _metric("SL.UEM.TOTL.ZS", "unemployment"),
        }

    def get_readiness(self) -> RepositoryReadiness:
        return RepositoryReadiness(ready=True, current_run_id="run-1")

    def get_current_run(self) -> CurrentRunSummary | None:
        return CurrentRunSummary(
            run_id="run-1",
            loaded_at=datetime(2026, 8, 18, tzinfo=UTC),
            country_count=3,
            indicator_count=2,
            observation_count=12,
            start_year=2023,
            end_year=2024,
            source_ids=(2,),
        )

    def get_countries(
        self,
        countries: tuple[str, ...] | list[str],
    ) -> tuple[CountryCatalogEntry, ...]:
        return tuple(self.countries[code] for code in countries if code in self.countries)

    def search_countries(
        self,
        *,
        query: str = "",
        region: str | None = None,
        income_level: str | None = None,
        additional_country_codes: tuple[str, ...] | list[str] = (),
        limit: int = 20,
    ) -> tuple[CountryCatalogEntry, ...]:
        query_folded = query.casefold()
        matches = [
            item
            for item in self.countries.values()
            if (
                not query_folded
                or query_folded in item.country_code.casefold()
                or query_folded in item.country_name.casefold()
                or item.country_code in additional_country_codes
            )
            and (region is None or item.region_name == region)
            and (income_level is None or item.income_level_name == income_level)
        ]
        return tuple(matches[:limit])

    def search_indicators(
        self,
        *,
        query: str,
        categories: tuple[str, ...] | list[str] = (),
        limit: int = 20,
    ) -> tuple[IndicatorCatalogEntry, ...]:
        matches = []
        for metric in self.metrics.values():
            if (
                query.casefold()
                not in (
                    f"{metric.indicator_code} {metric.alias} {metric.indicator_name}"
                ).casefold()
            ):
                continue
            if categories and metric.category not in categories:
                continue
            matches.append(
                IndicatorCatalogEntry(
                    run_id=metric.run_id,
                    source_id=metric.source_id,
                    indicator_code=metric.indicator_code,
                    alias=metric.alias,
                    indicator_name=metric.indicator_name,
                    category=metric.category,
                    unit=metric.unit,
                    display_unit=metric.display_unit,
                    dimensions_json=("{}",),
                )
            )
        return tuple(matches[:limit])

    def get_timeseries(
        self,
        *,
        countries: tuple[str, ...] | list[str],
        metrics: tuple[Any, ...] | list[Any],
        start_year: int | None = None,
        end_year: int | None = None,
    ) -> TimeseriesResult:
        selector = metrics[0].selector if hasattr(metrics[0], "selector") else str(metrics[0])
        metric = self.metrics[selector]
        base = {
            "DEU": (50.0, 52.0),
            "FRA": (45.0, 47.0),
            "POL": (25.0, 28.0),
        }
        if metric.indicator_code == "SL.UEM.TOTL.ZS":
            base = {"DEU": (4.0, 3.5), "FRA": (7.0, 6.5), "POL": (5.0, 4.5)}
        points = tuple(
            _point(
                country,
                year,
                base[country][year - 2023],
                code=metric.indicator_code,
                alias=metric.alias or metric.indicator_code,
            )
            for country in countries
            for year in (2023, 2024)
            if (start_year is None or year >= start_year) and (end_year is None or year <= end_year)
        )
        coverage = tuple(
            SeriesCoverage(
                country_code=country,
                source_id=metric.source_id,
                indicator_code=metric.indicator_code,
                dimensions_json="{}",
                expected_years=2,
                row_count=2,
                non_null_count=2,
                coverage_ratio=1.0,
                missing_years=(),
            )
            for country in countries
        )
        return TimeseriesResult(
            run_id="run-1",
            countries=tuple(countries),
            metrics=(metric,),
            start_year=start_year,
            end_year=end_year,
            points=points,
            coverage=coverage,
        )

    def get_country_snapshot(
        self,
        *,
        countries: tuple[str, ...] | list[str],
        metrics: tuple[Any, ...] | list[Any],
        mode: str = "latest_available",
        year: int | None = None,
    ) -> CountrySnapshotResult:
        metric = self.metrics[str(metrics[0])]
        comparison_year = year or 2024
        points = tuple(
            SnapshotPoint(
                run_id="run-1",
                source_id=metric.source_id,
                indicator_code=metric.indicator_code,
                indicator_alias=metric.alias,
                indicator_name=metric.indicator_name,
                country_code=country,
                country_name=self.countries[country].country_name,
                observation_year=comparison_year,
                value=1.0,
            )
            for country in countries
        )
        return CountrySnapshotResult(
            mode=mode,  # type: ignore[arg-type]
            comparison_year=comparison_year,
            countries=tuple(countries),
            metrics=(metric,),
            points=points,
        )

    def get_data_quality(
        self,
        *,
        countries: tuple[str, ...] | list[str],
        metrics: tuple[Any, ...] | list[Any],
    ) -> DataQualityResult:
        metric = self.metrics[str(metrics[0])]
        entries = tuple(
            DataQualityEntry(
                run_id="run-1",
                source_id=metric.source_id,
                indicator_code=metric.indicator_code,
                indicator_alias=metric.alias,
                indicator_name=metric.indicator_name,
                indicator_category=metric.category,
                country_code=country,
                country_name=self.countries[country].country_name,
                row_count=2,
                non_null_count=2,
                null_count=0,
                expected_years=2,
                coverage_ratio=1.0,
                first_available_year=2023,
                latest_available_year=2024,
            )
            for country in countries
        )
        return DataQualityResult(
            countries=tuple(countries),
            metrics=(metric,),
            entries=entries,
        )


def test_search_countries_includes_russian_label() -> None:
    service = ToolService(FakeToolRepository(), country_labels={"DEU": "Германия"})

    result = service.search_countries(SearchCountriesRequest(query="Герман"))

    assert result.matches[0].country_code == "DEU"
    assert result.matches[0].country_name_ru == "Германия"


def test_search_indicators_returns_source_qualified_metric() -> None:
    service = ToolService(FakeToolRepository())

    result = service.search_indicators(SearchIndicatorsRequest(query="gdp"))

    assert result.matches[0].metric_key == "2:NY.GDP.PCAP.CD"
    assert result.matches[0].dimensions_json == ("{}",)


def test_timeseries_rejects_country_absent_from_active_run() -> None:
    service = ToolService(FakeToolRepository())

    with pytest.raises(CountryNotFoundError, match="NLD"):
        service.get_timeseries(
            TimeseriesToolRequest(countries=("DEU", "NLD"), metrics=("gdp_per_capita",))
        )


def test_service_composes_all_analytical_calculations() -> None:
    service = ToolService(FakeToolRepository())

    timeseries = service.get_timeseries(
        TimeseriesToolRequest(countries=("DEU", "FRA"), metrics=("gdp_per_capita",))
    )
    snapshot = service.get_country_snapshot(
        SnapshotToolRequest(countries=("DEU", "FRA"), metrics=("gdp_per_capita",))
    )
    trend = service.calculate_trend(TrendToolRequest(country="DEU", metric="gdp_per_capita"))
    comparison = service.compare_countries(
        CompareCountriesToolRequest(
            countries=("DEU", "FRA", "POL"),
            metric="gdp_per_capita",
        )
    )
    correlation = service.calculate_correlation(
        CorrelationToolRequest(
            countries=("DEU", "FRA", "POL"),
            x_metric="gdp_per_capita",
            y_metric="unemployment",
        )
    )
    quality = service.get_data_quality(
        DataQualityToolRequest(countries=("DEU",), metrics=("gdp_per_capita",))
    )

    assert len(timeseries.points) == 4
    assert snapshot.comparison_year == 2024
    assert trend.absolute_change == pytest.approx(2.0)
    assert comparison.year == 2024
    assert correlation.sample_size == 6
    assert len(quality.entries) == 1
