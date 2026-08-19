from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from wb_insight.analytics import (
    AmbiguousMetricError,
    CorrelationResult,
    CountryComparisonResult,
    CountrySnapshotResult,
    CurrentRunSummary,
    DataQualityResult,
    DimensionRequiredError,
    MetricNotFoundError,
    RepositoryReadiness,
    ResultLimitError,
    TimeseriesResult,
    TrendResult,
)
from wb_insight.api.dependencies import get_tool_service
from wb_insight.api.main import create_app
from wb_insight.config import AppSettings
from wb_insight.tools import (
    AnalyticsUnavailableError,
    CountryNotFoundError,
    CountrySearchResult,
    IndicatorSearchResult,
    SearchCountriesRequest,
    SearchIndicatorsRequest,
)


class FakeApiService:
    def __init__(self) -> None:
        self.ready = True
        self.failure: Exception | None = None

    def _fail(self) -> None:
        if self.failure is not None:
            raise self.failure

    def readiness(self) -> RepositoryReadiness:
        self._fail()
        return RepositoryReadiness(
            ready=self.ready,
            current_run_id="run-1" if self.ready else None,
            missing_objects=() if self.ready else ("mart_data_quality",),
        )

    def current_run(self) -> CurrentRunSummary:
        self._fail()
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

    def search_countries(self, request: SearchCountriesRequest) -> CountrySearchResult:
        self._fail()
        return CountrySearchResult(query=request.query, matches=())

    def search_indicators(self, request: SearchIndicatorsRequest) -> IndicatorSearchResult:
        self._fail()
        return IndicatorSearchResult(query=request.query, matches=())

    def get_timeseries(self, _: object) -> TimeseriesResult:
        self._fail()
        return TimeseriesResult(
            run_id="run-1",
            countries=("DEU",),
            metrics=(),
            start_year=2023,
            end_year=2024,
            points=(),
            coverage=(),
        )

    def get_country_snapshot(self, _: object) -> CountrySnapshotResult:
        self._fail()
        return CountrySnapshotResult(
            mode="common_year",
            comparison_year=2024,
            countries=("DEU",),
            metrics=(),
            points=(),
        )

    def calculate_trend(self, _: object) -> TrendResult:
        self._fail()
        return TrendResult(
            run_id="run-1",
            country_code="DEU",
            source_id=2,
            indicator_code="NY.GDP.PCAP.CD",
            observation_count=2,
            missing_count=0,
        )

    def compare_countries(self, _: object) -> CountryComparisonResult:
        self._fail()
        return CountryComparisonResult(
            run_id="run-1",
            source_id=2,
            indicator_code="NY.GDP.PCAP.CD",
            year=2024,
            descending=True,
            mean=None,
            median=None,
            entries=(),
        )

    def calculate_correlation(self, _: object) -> CorrelationResult:
        self._fail()
        return CorrelationResult(
            method="pearson",
            x_source_id=2,
            x_indicator_code="NY.GDP.PCAP.CD",
            y_source_id=2,
            y_indicator_code="SL.UEM.TOTL.ZS",
            coefficient=None,
            sample_size=0,
            dropped_pairs=0,
            countries_used=(),
            years_used=(),
        )

    def get_data_quality(self, _: object) -> DataQualityResult:
        self._fail()
        return DataQualityResult(countries=("DEU",), metrics=(), entries=())


def _client(service: FakeApiService | None = None) -> tuple[TestClient, FakeApiService]:
    fake = service or FakeApiService()
    settings = AppSettings(
        _env_file=None,
        clickhouse_password="unused",
        marts_config_path="configs/marts.yaml",
    )
    app = create_app(settings)
    app.dependency_overrides[get_tool_service] = lambda: fake
    return TestClient(app, raise_server_exceptions=False), fake


def test_liveness_does_not_require_analytical_service() -> None:
    client, service = _client()
    service.failure = RuntimeError("must not be called")

    response = client.get("/health/live")

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["status"] == "ok"
    assert payload["tool"] == "health_live"
    UUID(payload["request_id"])
    assert response.headers["X-Request-ID"] == payload["request_id"]


def test_ready_and_current_run_use_stable_envelopes() -> None:
    client, _ = _client()

    ready = client.get("/health/ready")
    current = client.get("/v1/meta/current-run")

    assert ready.status_code == 200
    assert ready.json()["data"]["current_run_id"] == "run-1"
    assert current.status_code == 200
    assert current.json()["data"]["country_count"] == 3


def test_all_tool_routes_are_exposed() -> None:
    client, _ = _client()
    calls = [
        ("/v1/tools/search-countries", {"query": "Germany"}),
        ("/v1/tools/search-indicators", {"query": "GDP"}),
        (
            "/v1/tools/timeseries",
            {"countries": ["DEU"], "metrics": ["gdp_per_capita"]},
        ),
        (
            "/v1/tools/country-snapshot",
            {
                "countries": ["DEU"],
                "metrics": ["gdp_per_capita"],
                "mode": "common_year",
            },
        ),
        ("/v1/tools/trend", {"country": "DEU", "metric": "gdp_per_capita"}),
        (
            "/v1/tools/compare-countries",
            {"countries": ["DEU", "FRA"], "metric": "gdp_per_capita"},
        ),
        (
            "/v1/tools/correlation",
            {
                "countries": ["DEU"],
                "x_metric": "gdp_per_capita",
                "y_metric": "unemployment",
            },
        ),
        (
            "/v1/tools/data-quality",
            {"countries": ["DEU"], "metrics": ["gdp_per_capita"]},
        ),
    ]

    for path, payload in calls:
        response = client.post(path, json=payload)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["request_id"]
        assert body["elapsed_ms"] >= 0
        assert "data" in body


def test_request_validation_uses_public_error_envelope() -> None:
    client, _ = _client()

    response = client.post("/v1/tools/timeseries", json={"countries": [], "metrics": []})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["request_id"] == response.headers["X-Request-ID"]
    assert error["details"]["errors"]


def test_metric_not_found_maps_to_404_without_internal_details() -> None:
    client, service = _client()
    service.failure = MetricNotFoundError("Metric 'missing' was not found.")

    response = client.post(
        "/v1/tools/timeseries",
        json={"countries": ["DEU"], "metrics": ["missing"]},
    )

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "metric_not_found"
    assert "Traceback" not in response.text
    assert "SELECT" not in response.text


def test_readiness_returns_503_when_marts_are_missing() -> None:
    client, service = _client()
    service.ready = False

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "analytics_unavailable"
    assert response.json()["error"]["details"]["missing_objects"] == ["mart_data_quality"]


def test_valid_incoming_request_id_is_preserved() -> None:
    client, _ = _client()
    incoming = "a1dc7b95-c104-426a-a8f1-8e2984ea2630"

    response = client.get("/health/live", headers={"X-Request-ID": incoming})

    assert response.headers["X-Request-ID"] == incoming
    assert response.json()["request_id"] == incoming


@pytest.mark.parametrize(
    ("failure", "status_code", "error_code"),
    [
        (AmbiguousMetricError("ambiguous"), 409, "ambiguous_metric"),
        (DimensionRequiredError("dimension required"), 409, "dimension_required"),
        (ResultLimitError("too many rows"), 422, "result_limit_exceeded"),
        (CountryNotFoundError("country missing"), 404, "country_not_found"),
        (
            AnalyticsUnavailableError("backend unavailable"),
            503,
            "analytics_unavailable",
        ),
        (ValueError("invalid request"), 422, "invalid_request"),
    ],
)
def test_expected_failures_use_stable_error_codes(
    failure: Exception,
    status_code: int,
    error_code: str,
) -> None:
    client, service = _client()
    service.failure = failure

    response = client.post(
        "/v1/tools/timeseries",
        json={"countries": ["DEU"], "metrics": ["gdp_per_capita"]},
    )

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == error_code
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


def test_unexpected_error_is_masked() -> None:
    client, service = _client()
    service.failure = RuntimeError("SELECT password FROM private_table")

    response = client.post(
        "/v1/tools/timeseries",
        json={"countries": ["DEU"], "metrics": ["gdp_per_capita"]},
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "SELECT" not in response.text
    assert "password" not in response.text
    assert "private_table" not in response.text
