"""Smoke-test the WB Insight Tool API in-process or over HTTP."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol

import httpx
from fastapi.testclient import TestClient

from wb_insight.api.main import create_app


class ApiClient(Protocol):
    """Small HTTP client surface used by smoke checks."""

    def get(self, url: str, **kwargs: Any) -> httpx.Response: ...

    def post(self, url: str, **kwargs: Any) -> httpx.Response: ...


def main() -> None:
    with _client() as client:
        live = _checked("live", client.get("/health/live"))
        ready = _checked("ready", client.get("/health/ready"))
        current = _checked("current_run", client.get("/v1/meta/current-run"))

        countries_search = _checked(
            "search_countries",
            client.post(
                "/v1/tools/search-countries",
                json={"query": "", "limit": 3},
            ),
        )
        country_matches = countries_search.json()["data"]["matches"]
        if len(country_matches) < 2:
            raise RuntimeError("Tool API smoke-test requires at least two countries.")
        countries = [item["country_code"] for item in country_matches[:3]]

        gdp_search = _checked(
            "search_gdp",
            client.post(
                "/v1/tools/search-indicators",
                json={"query": "GDP per capita", "limit": 5},
            ),
        )
        unemployment_search = _checked(
            "search_unemployment",
            client.post(
                "/v1/tools/search-indicators",
                json={"query": "Unemployment", "limit": 5},
            ),
        )
        gdp_metric = _first_metric_key(gdp_search)
        unemployment_metric = _first_metric_key(unemployment_search)

        current_data = current.json()["data"]
        end_year = int(current_data["end_year"])
        start_year = max(int(current_data["start_year"]), end_year - 4)

        _checked(
            "timeseries",
            client.post(
                "/v1/tools/timeseries",
                json={
                    "countries": countries,
                    "metrics": [gdp_metric],
                    "start_year": start_year,
                    "end_year": end_year,
                },
            ),
        )
        _checked(
            "snapshot",
            client.post(
                "/v1/tools/country-snapshot",
                json={
                    "countries": countries,
                    "metrics": [gdp_metric],
                    "mode": "common_year",
                },
            ),
        )
        _checked(
            "trend",
            client.post(
                "/v1/tools/trend",
                json={
                    "country": countries[0],
                    "metric": gdp_metric,
                    "start_year": start_year,
                    "end_year": end_year,
                },
            ),
        )
        _checked(
            "comparison",
            client.post(
                "/v1/tools/compare-countries",
                json={
                    "countries": countries,
                    "metric": gdp_metric,
                },
            ),
        )
        _checked(
            "correlation",
            client.post(
                "/v1/tools/correlation",
                json={
                    "countries": countries,
                    "x_metric": gdp_metric,
                    "y_metric": unemployment_metric,
                    "start_year": start_year,
                    "end_year": end_year,
                    "min_observations": 3,
                },
            ),
        )
        _checked(
            "data_quality",
            client.post(
                "/v1/tools/data-quality",
                json={
                    "countries": countries,
                    "metrics": [gdp_metric, unemployment_metric],
                },
            ),
        )

        # Keep references alive until all checks have completed in TestClient mode.
        _ = (live, ready)


@contextmanager
def _client() -> Iterator[ApiClient]:
    base_url = os.getenv("WB_TOOL_API_URL")
    if base_url:
        with httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(20.0),
        ) as client:
            yield client
        return

    with TestClient(create_app()) as client:
        yield client


def _checked(name: str, response: httpx.Response) -> httpx.Response:
    if response.is_error:
        response_body = response.text[:2000]
        print(f"{name}: status={response.status_code} body={response_body}")
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    request_id = payload.get("request_id", "missing")
    elapsed = payload.get("elapsed_ms", "missing")
    print(f"{name}: status={response.status_code} request_id={request_id} elapsed_ms={elapsed}")
    return response


def _first_metric_key(response: httpx.Response) -> str:
    matches = response.json()["data"]["matches"]
    if not matches:
        raise RuntimeError("Tool API smoke-test could not resolve a required metric.")
    return str(matches[0]["metric_key"])


if __name__ == "__main__":
    main()
