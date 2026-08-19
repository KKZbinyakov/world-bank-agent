from __future__ import annotations

from fastapi.testclient import TestClient

from wb_insight.api.main import create_app
from wb_insight.config import AppSettings

EXPECTED_TOOL_OPERATIONS = {
    "search_countries_v1",
    "search_indicators_v1",
    "get_timeseries_v1",
    "get_country_snapshot_v1",
    "calculate_trend_v1",
    "compare_countries_v1",
    "calculate_correlation_v1",
    "get_data_quality_v1",
}


def test_openapi_contract_has_versioned_tools_and_no_sql_input() -> None:
    app = create_app(
        AppSettings(_env_file=None, clickhouse_password="unused", api_docs_enabled=True)
    )
    schema = TestClient(app).get("/openapi.json").json()

    operation_ids = {
        operation["operationId"]
        for path_item in schema["paths"].values()
        for method, operation in path_item.items()
        if method in {"get", "post", "put", "patch", "delete"}
    }
    assert operation_ids >= EXPECTED_TOOL_OPERATIONS
    assert len(EXPECTED_TOOL_OPERATIONS) == 8
    component_names = set(schema["components"]["schemas"])
    assert "ErrorResponse" in component_names
    assert "TimeseriesResult" in component_names
    assert any(name.startswith("ToolResponse_") for name in component_names)

    serialized = str(schema).lower()
    assert '"sql"' not in serialized
    assert '"table"' not in serialized


def test_public_paths_are_intentional() -> None:
    app = create_app(AppSettings(_env_file=None, clickhouse_password="unused"))
    paths = set(TestClient(app).get("/openapi.json").json()["paths"])

    assert paths == {
        "/health/live",
        "/health/ready",
        "/v1/meta/current-run",
        "/v1/tools/search-countries",
        "/v1/tools/search-indicators",
        "/v1/tools/timeseries",
        "/v1/tools/country-snapshot",
        "/v1/tools/trend",
        "/v1/tools/compare-countries",
        "/v1/tools/correlation",
        "/v1/tools/data-quality",
    }
