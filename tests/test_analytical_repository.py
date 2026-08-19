from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from wb_insight.analytics import (
    AmbiguousMetricError,
    AnalyticalRepository,
    DimensionRequiredError,
    MetricRequest,
    ResultLimitError,
)


class FakeResult:
    def __init__(self, rows: list[list[Any] | tuple[Any, ...]]) -> None:
        self._rows = rows

    @property
    def result_set(self) -> list[list[Any] | tuple[Any, ...]]:
        return self._rows


class QueueClient:
    def __init__(self, responses: Sequence[list[list[Any] | tuple[Any, ...]]]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, Mapping[str, Any] | Sequence[Any] | None]] = []
        self.closed = False

    def query(
        self,
        query: str,
        parameters: Mapping[str, Any] | Sequence[Any] | None = None,
    ) -> FakeResult:
        self.calls.append((query, parameters))
        if not self.responses:
            raise AssertionError("unexpected analytical query")
        return FakeResult(self.responses.pop(0))

    def close(self) -> None:
        self.closed = True


def _metric_row(
    *,
    source_id: int = 2,
    code: str = "NY.GDP.PCAP.CD",
    alias: str = "gdp_per_capita_current_usd",
    dimensions: str = "{}",
) -> tuple[Any, ...]:
    return (
        "run-1",
        source_id,
        code,
        alias,
        "GDP per capita (current US$)",
        "ВВП на душу населения",
        "economy",
        "current_usd_per_person",
        "US$ / person",
        dimensions,
    )


def _timeseries_row(
    country: str,
    year: int,
    value: float | None,
    *,
    code: str = "NY.GDP.PCAP.CD",
    alias: str = "gdp_per_capita_current_usd",
) -> tuple[Any, ...]:
    country_name = {"DEU": "Germany", "NLD": "Netherlands"}[country]
    return (
        "run-1",
        2,
        code,
        alias,
        "GDP per capita (current US$)",
        "ВВП на душу населения",
        "economy",
        "current_usd_per_person",
        "US$ / person",
        country,
        country_name,
        "Europe & Central Asia",
        "High income",
        year,
        value,
        "{}",
        value is None,
    )


def _snapshot_row(country: str, year: int | None, value: float | None) -> tuple[Any, ...]:
    country_name = {"DEU": "Germany", "NLD": "Netherlands"}[country]
    return (
        "run-1",
        2,
        "NY.GDP.PCAP.CD",
        "gdp_per_capita_current_usd",
        "GDP per capita (current US$)",
        "ВВП на душу населения",
        "economy",
        "current_usd_per_person",
        "US$ / person",
        country,
        country_name,
        "Europe & Central Asia",
        "High income",
        year,
        value,
        "{}",
    )


def _quality_row(country: str, coverage: float) -> tuple[Any, ...]:
    country_name = {"DEU": "Germany", "NLD": "Netherlands"}[country]
    return (
        "run-1",
        2,
        "NY.GDP.PCAP.CD",
        "gdp_per_capita_current_usd",
        "GDP per capita (current US$)",
        "economy",
        country,
        country_name,
        "{}",
        10,
        int(coverage * 10),
        10 - int(coverage * 10),
        10,
        coverage,
        2015,
        2024 if coverage == 1 else 2023,
    )


def test_metric_request_canonicalizes_dimensions() -> None:
    request = MetricRequest(
        selector=" 6:DT.DOD.DECT.CD ",
        dimensions={"Counterpart-Area": " WLD ", "Version": "2024"},
    )

    assert request.selector == "6:DT.DOD.DECT.CD"
    assert request.dimensions_json == '{"Counterpart-Area":"WLD","Version":"2024"}'


def test_resolve_metric_supports_explicit_source_code() -> None:
    client = QueueClient([[_metric_row()]])
    repository = AnalyticalRepository(client)

    metrics = repository.resolve_metrics(["2:NY.GDP.PCAP.CD"])

    assert len(metrics) == 1
    assert metrics[0].metric_key == "2:NY.GDP.PCAP.CD"
    query, parameters = client.calls[0]
    assert "source_id = {source_id:Int32}" in query
    assert parameters == {"source_id": 2, "indicator_code": "NY.GDP.PCAP.CD"}


def test_metric_selector_is_bound_as_a_parameter() -> None:
    selector = "gdp' OR 1 = 1 --"
    client = QueueClient([[_metric_row()]])
    repository = AnalyticalRepository(client)

    repository.resolve_metrics([selector])

    query, parameters = client.calls[0]
    assert selector not in query
    assert parameters == {"selector": selector}


def test_resolve_metric_rejects_ambiguous_code() -> None:
    client = QueueClient(
        [
            [
                _metric_row(),
                _metric_row(source_id=99, alias="another_gdp"),
            ]
        ]
    )
    repository = AnalyticalRepository(client)

    with pytest.raises(AmbiguousMetricError, match="ambiguous"):
        repository.resolve_metrics(["NY.GDP.PCAP.CD"])


def test_resolve_metric_matches_advanced_dimension_id() -> None:
    dimensions = '{"Counterpart-Area":{"id":"WLD","value":"World"}}'
    client = QueueClient(
        [
            [
                _metric_row(
                    source_id=6,
                    code="DT.DOD.DECT.CD",
                    alias="external_debt",
                    dimensions=dimensions,
                )
            ]
        ]
    )
    repository = AnalyticalRepository(client)

    metrics = repository.resolve_metrics(
        [
            MetricRequest(
                selector="6:DT.DOD.DECT.CD",
                dimensions={"Counterpart-Area": "WLD"},
            )
        ]
    )

    assert metrics[0].dimensions_json == dimensions


def test_resolve_metric_reports_available_dimensions() -> None:
    client = QueueClient([[_metric_row(dimensions='{"Counterpart-Area":"WLD"}')]])
    repository = AnalyticalRepository(client)

    with pytest.raises(DimensionRequiredError, match="explicit dimension"):
        repository.resolve_metrics(["2:NY.GDP.PCAP.CD"])


def test_get_timeseries_returns_evidence_and_coverage() -> None:
    client = QueueClient(
        [
            [_metric_row()],
            [
                _timeseries_row("DEU", 2022, 48_000.0),
                _timeseries_row("DEU", 2023, None),
                _timeseries_row("DEU", 2024, 52_000.0),
                _timeseries_row("NLD", 2022, 58_000.0),
                _timeseries_row("NLD", 2023, 60_000.0),
                _timeseries_row("NLD", 2024, 62_000.0),
            ],
        ]
    )
    repository = AnalyticalRepository(client)

    result = repository.get_timeseries(
        countries=["deu", "NLD"],
        metrics=["gdp_per_capita_current_usd"],
        start_year=2022,
        end_year=2024,
    )

    assert result.run_id == "run-1"
    assert result.countries == ("DEU", "NLD")
    assert len(result.points) == 6
    assert result.points[0].display_unit == "US$ / person"
    deu = next(item for item in result.coverage if item.country_code == "DEU")
    assert deu.expected_years == 3
    assert deu.non_null_count == 2
    assert deu.missing_years == (2023,)
    assert result.warnings == ("1 series contain missing years or null values.",)
    _, parameters = client.calls[1]
    assert isinstance(parameters, Mapping)
    assert parameters["countries"] == ["DEU", "NLD"]
    assert parameters["start_year"] == 2022
    assert parameters["end_year"] == 2024


def test_get_timeseries_enforces_result_limit() -> None:
    client = QueueClient(
        [
            [_metric_row()],
            [
                _timeseries_row("DEU", 2022, 48_000.0),
                _timeseries_row("DEU", 2023, 50_000.0),
            ],
        ]
    )
    repository = AnalyticalRepository(client, max_result_rows=1)

    with pytest.raises(ResultLimitError, match="narrow the scope"):
        repository.get_timeseries(countries=["DEU"], metrics=["2:NY.GDP.PCAP.CD"])


def test_get_latest_country_snapshot_preserves_observation_years() -> None:
    client = QueueClient(
        [
            [_metric_row()],
            [
                _snapshot_row("DEU", 2024, 52_000.0),
                _snapshot_row("NLD", 2023, 60_000.0),
            ],
        ]
    )
    repository = AnalyticalRepository(client)

    result = repository.get_country_snapshot(
        countries=["DEU", "NLD"],
        metrics=["2:NY.GDP.PCAP.CD"],
    )

    assert result.mode == "latest_available"
    assert result.comparison_year is None
    assert [point.observation_year for point in result.points] == [2024, 2023]
    assert not result.warnings


def test_get_common_year_snapshot_uses_latest_complete_year() -> None:
    client = QueueClient(
        [
            [_metric_row()],
            [[2023]],
            [
                _snapshot_row("DEU", 2023, 50_000.0),
                _snapshot_row("NLD", 2023, 60_000.0),
            ],
        ]
    )
    repository = AnalyticalRepository(client)

    result = repository.get_country_snapshot(
        countries=["DEU", "NLD"],
        metrics=["2:NY.GDP.PCAP.CD"],
        mode="common_year",
    )

    assert result.comparison_year == 2023
    assert len(result.points) == 2
    assert "uniqExact" in client.calls[1][0]


def test_get_common_year_snapshot_reports_absence() -> None:
    client = QueueClient([[_metric_row()], [[None]]])
    repository = AnalyticalRepository(client)

    result = repository.get_country_snapshot(
        countries=["DEU", "NLD"],
        metrics=["2:NY.GDP.PCAP.CD"],
        mode="common_year",
    )

    assert result.comparison_year is None
    assert not result.points
    assert len(result.missing_pairs) == 2
    assert "No year" in result.warnings[0]
    assert "nullIf(max(year), 0)" in client.calls[1][0]


def test_get_data_quality_returns_dimension_aware_rows() -> None:
    client = QueueClient(
        [
            [_metric_row()],
            [_quality_row("DEU", 1.0), _quality_row("NLD", 0.8)],
        ]
    )
    repository = AnalyticalRepository(client)

    result = repository.get_data_quality(
        countries=["DEU", "NLD"],
        metrics=["2:NY.GDP.PCAP.CD"],
    )

    assert len(result.entries) == 2
    assert result.entries[0].dimensions_json == "{}"
    assert result.entries[1].null_count == 2
    assert result.warnings == ("1 series have coverage below 100%.",)


def test_repository_validates_country_and_period_limits() -> None:
    repository = AnalyticalRepository(QueueClient([]), max_countries=1, max_years=2)

    with pytest.raises(ValueError, match="at most 1 countries"):
        repository.get_timeseries(countries=["DEU", "NLD"], metrics=["x"])
    with pytest.raises(ValueError, match="invalid ISO3"):
        repository.get_timeseries(countries=["GERMANY"], metrics=["x"])

    client = QueueClient([[_metric_row()]])
    repository = AnalyticalRepository(client, max_years=2)
    with pytest.raises(ValueError, match="at most 2 years"):
        repository.get_timeseries(
            countries=["DEU"],
            metrics=["2:NY.GDP.PCAP.CD"],
            start_year=2020,
            end_year=2024,
        )


def test_repository_context_manager_closes_client() -> None:
    client = QueueClient([])

    with AnalyticalRepository(client):
        pass

    assert client.closed is True
