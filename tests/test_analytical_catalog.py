from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from wb_insight.analytics import (
    AnalyticalRepository,
    DimensionRequiredError,
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
        return None


def test_readiness_requires_objects_and_active_run() -> None:
    loaded_at = datetime(2026, 8, 18, tzinfo=UTC)
    client = QueueClient(
        [
            [
                ["etl_run"],
                ["mart_country_snapshot"],
                ["mart_data_quality"],
                ["mart_indicator_timeseries"],
            ],
            [["run-1", loaded_at, 3, 2, 12, 2023, 2024, [2, 3]]],
        ]
    )
    repository = AnalyticalRepository(client)

    result = repository.get_readiness()

    assert result.ready is True
    assert result.current_run_id == "run-1"
    assert not result.missing_objects


def test_readiness_reports_missing_mart_without_querying_run() -> None:
    client = QueueClient([[["etl_run"], ["mart_indicator_timeseries"]]])
    repository = AnalyticalRepository(client)

    result = repository.get_readiness()

    assert result.ready is False
    assert result.current_run_id is None
    assert set(result.missing_objects) == {"mart_country_snapshot", "mart_data_quality"}
    assert len(client.calls) == 1


def test_get_current_run_returns_none_when_no_loaded_run_exists() -> None:
    repository = AnalyticalRepository(QueueClient([[]]))

    assert repository.get_current_run() is None


def test_get_countries_uses_bound_array_parameter() -> None:
    client = QueueClient(
        [
            [
                (
                    "run-1",
                    "DEU",
                    "Germany",
                    "Europe & Central Asia",
                    "High income",
                    13.4,
                    52.5,
                )
            ]
        ]
    )
    repository = AnalyticalRepository(client)

    result = repository.get_countries(["deu"])

    assert result[0].country_code == "DEU"
    query, parameters = client.calls[0]
    assert "DEU" not in query
    assert "FROM mart_indicator_timeseries AS t" in query
    assert "WHERE t.run_id = current_run" in query
    assert "any(run_id) AS run_id" not in query
    assert parameters == {"countries": ["DEU"]}


def test_search_countries_blank_query_uses_discovery_path() -> None:
    client = QueueClient(
        [
            [
                (
                    "run-1",
                    "DEU",
                    "Germany",
                    "Europe & Central Asia",
                    "High income",
                    13.4,
                    52.5,
                )
            ]
        ]
    )
    repository = AnalyticalRepository(client)

    result = repository.search_countries(query="", limit=3)

    assert result[0].country_code == "DEU"
    query, parameters = client.calls[0]
    assert "positionCaseInsensitiveUTF8" not in query
    assert "startsWith" not in query
    assert "FROM mart_indicator_timeseries AS t" in query
    assert "WHERE t.run_id = current_run" in query
    assert "any(run_id) AS run_id" not in query
    assert isinstance(parameters, Mapping)
    assert "query" not in parameters
    assert "additional_country_codes" not in parameters
    assert parameters["limit"] == 3


def test_search_countries_supports_label_codes_and_filters() -> None:
    client = QueueClient(
        [
            [
                (
                    "run-1",
                    "DEU",
                    "Germany",
                    "Europe & Central Asia",
                    "High income",
                    13.4,
                    52.5,
                )
            ]
        ]
    )
    repository = AnalyticalRepository(client)

    result = repository.search_countries(
        query="Германия",
        region="Europe & Central Asia",
        additional_country_codes=["DEU"],
        limit=5,
    )

    assert result[0].country_name == "Germany"
    _, parameters = client.calls[0]
    assert isinstance(parameters, Mapping)
    assert parameters["additional_country_codes"] == ["DEU"]
    assert parameters["limit"] == 5


def test_search_indicators_returns_dimension_slices() -> None:
    client = QueueClient(
        [
            [
                (
                    "run-1",
                    6,
                    "DT.DOD.DECT.CD",
                    "external_debt",
                    "External debt stocks, total",
                    "Внешний долг",
                    "debt",
                    "current_usd",
                    "US$",
                    ['{"Counterpart-Area":"WLD"}'],
                )
            ]
        ]
    )
    repository = AnalyticalRepository(client)

    result = repository.search_indicators(query="debt", categories=["debt"])

    assert result[0].metric_key == "6:DT.DOD.DECT.CD"
    assert result[0].dimensions_json == ('{"Counterpart-Area":"WLD"}',)
    query, parameters = client.calls[0]
    assert "debt" not in query
    assert "FROM mart_indicator_timeseries AS t" in query
    assert "WHERE t.run_id = current_run" in query
    assert "any(run_id) AS run_id" not in query
    assert isinstance(parameters, Mapping)
    assert parameters["categories"] == ["debt"]


def test_multidimensional_metric_requires_explicit_slice() -> None:
    client = QueueClient(
        [
            [
                (
                    "run-1",
                    6,
                    "DT.DOD.DECT.CD",
                    "external_debt",
                    "External debt stocks, total",
                    None,
                    "debt",
                    "current_usd",
                    "US$",
                    '{"Counterpart-Area":"WLD"}',
                )
            ]
        ]
    )
    repository = AnalyticalRepository(client)

    with pytest.raises(DimensionRequiredError, match="explicit dimension"):
        repository.resolve_metrics(["6:DT.DOD.DECT.CD"])
