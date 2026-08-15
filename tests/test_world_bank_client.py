"""Unit tests for the World Bank Indicators API client."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from wb_insight.ingestion.schemas import WorldBankAPIError, WorldBankResponseError
from wb_insight.ingestion.world_bank_client import WorldBankClient

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = "https://api.worldbank.org/v2"


def load_fixture(name: str) -> Any:
    with (FIXTURES / name).open(encoding="utf-8") as file:
        return json.load(file)


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    per_page: int = 1000,
    max_attempts: int = 3,
) -> WorldBankClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(base_url=BASE_URL, transport=transport)
    return WorldBankClient(
        per_page=per_page,
        max_attempts=max_attempts,
        retry_wait_seconds=0,
        http_client=http_client,
    )


def test_get_countries_follows_pagination() -> None:
    requested_pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/country"
        assert request.url.params["format"] == "json"
        assert request.url.params["per_page"] == "2"
        page = int(request.url.params["page"])
        requested_pages.append(page)
        fixture = "countries_page_1.json" if page == 1 else "countries_page_2.json"
        return httpx.Response(200, json=load_fixture(fixture))

    client = make_client(handler, per_page=2)
    countries = client.get_countries()

    assert [country["id"] for country in countries] == ["DEU", "NLD", "POL"]
    assert requested_pages == [1, 2]


def test_get_indicators_passes_source_filter() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/indicator"
        assert request.url.params["source"] == "2"
        return httpx.Response(200, json=load_fixture("indicators_page_1.json"))

    client = make_client(handler)
    indicators = client.get_indicators(source_id=2)

    assert len(indicators) == 2
    assert indicators[0]["id"] == "NY.GDP.PCAP.CD"


def test_get_observations_builds_multi_country_multi_indicator_query() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == ("/v2/country/DEU;NLD/indicator/NY.GDP.PCAP.CD;SP.POP.TOTL")
        assert request.url.params["date"] == "2024:2024"
        assert request.url.params["source"] == "2"
        return httpx.Response(200, json=load_fixture("observations_page_1.json"))

    client = make_client(handler)
    observations = client.get_observations(
        indicator_codes=["ny.gdp.pcap.cd", "sp.pop.totl"],
        country_codes=["deu", "nld"],
        start_year=2024,
        end_year=2024,
        source_id=2,
    )

    assert len(observations) == 4
    assert observations[0]["countryiso3code"] == "DEU"


def test_multiple_indicators_require_source_id() -> None:
    with (
        WorldBankClient(retry_wait_seconds=0) as client,
        pytest.raises(ValueError, match="source_id is required"),
    ):
        client.get_observations(
            indicator_codes=["NY.GDP.PCAP.CD", "SP.POP.TOTL"],
            country_codes=["DEU"],
            start_year=2020,
            end_year=2024,
        )


def test_retryable_server_error_is_retried() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, text="temporary failure")
        return httpx.Response(
            200,
            json=[{"page": 1, "pages": 1, "per_page": 1000, "total": 0}, []],
        )

    client = make_client(handler, max_attempts=2)
    assert client.get_countries() == []

    assert calls == 2


def test_non_retryable_http_error_raises_api_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"message": [{"id": "120", "key": "Invalid value", "value": "Bad request"}]},
        )

    client = make_client(handler)
    with pytest.raises(WorldBankAPIError, match="Bad request"):
        client.get_countries()


def test_api_error_object_with_http_200_raises_api_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"message": [{"id": "120", "key": "Invalid value", "value": "Invalid country"}]},
        )

    client = make_client(handler)
    with pytest.raises(WorldBankAPIError, match="Invalid country"):
        client.get_countries()


def test_invalid_json_raises_response_error() -> None:
    client = make_client(lambda _: httpx.Response(200, text="not-json"))
    with pytest.raises(WorldBankResponseError, match="invalid JSON"):
        client.get_countries()


def test_malformed_pagination_metadata_raises_response_error() -> None:
    client = make_client(lambda _: httpx.Response(200, json=[{"page": 1}, []]))
    with pytest.raises(WorldBankResponseError, match="pagination metadata"):
        client.get_countries()


def test_observation_arguments_are_validated() -> None:
    client = WorldBankClient(retry_wait_seconds=0)
    try:
        with pytest.raises(ValueError, match="country code"):
            client.get_observations(
                indicator_codes=["SP.POP.TOTL"],
                country_codes=["Germany"],
                start_year=2020,
                end_year=2024,
            )

        with pytest.raises(ValueError, match="end_year"):
            client.get_observations(
                indicator_codes=["SP.POP.TOTL"],
                country_codes=["DEU"],
                start_year=2024,
                end_year=2020,
            )
    finally:
        client.close()


def test_transport_error_is_retried_and_then_raised() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("offline", request=request)

    client = make_client(handler, max_attempts=2)
    with pytest.raises(WorldBankAPIError, match="after 2 attempts"):
        client.get_countries()
    assert calls == 2


def test_null_record_list_is_treated_as_empty() -> None:
    client = make_client(
        lambda _: httpx.Response(
            200,
            json=[{"page": 1, "pages": 1, "per_page": 1000, "total": 0}, None],
        )
    )
    assert client.get_countries() == []


def test_page_number_mismatch_is_rejected() -> None:
    client = make_client(
        lambda _: httpx.Response(
            200,
            json=[{"page": 2, "pages": 2, "per_page": 1000, "total": 1}, []],
        )
    )
    with pytest.raises(WorldBankResponseError, match="expected page 1"):
        client.get_countries()


def test_duplicate_codes_are_rejected() -> None:
    with (
        WorldBankClient(retry_wait_seconds=0) as client,
        pytest.raises(ValueError, match="indicator codes must be unique"),
    ):
        client.get_observations(
            indicator_codes=["SP.POP.TOTL", "sp.pop.totl"],
            country_codes=["DEU"],
            start_year=2020,
            end_year=2024,
            source_id=2,
        )


def test_more_than_sixty_indicators_are_rejected() -> None:
    indicators = [f"TEST.INDICATOR.{index}" for index in range(61)]
    with (
        WorldBankClient(retry_wait_seconds=0) as client,
        pytest.raises(ValueError, match="at most 60 indicators"),
    ):
        client.get_observations(
            indicator_codes=indicators,
            country_codes=["DEU"],
            start_year=2020,
            end_year=2024,
            source_id=2,
        )


def test_unexpected_json_list_shape_reports_response_shape() -> None:
    client = make_client(lambda _: httpx.Response(200, json=[]))
    with pytest.raises(WorldBankResponseError, match="list with 0 elements"):
        client.get_countries()


def test_get_source_concepts_parses_advanced_api_object() -> None:
    payload = {
        "page": 1,
        "pages": 1,
        "per_page": 1000,
        "total": 4,
        "source": [
            {
                "id": "6",
                "name": "International Debt Statistics",
                "concept": [
                    {"id": "Country", "value": "Country"},
                    {"id": "Series", "value": "Series"},
                    {"id": "Counterpart-Area", "value": "Counterpart Area"},
                    {"id": "Time", "value": "Time"},
                ],
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/sources/6/concepts/data"
        return httpx.Response(200, json=payload)

    client = make_client(handler)
    concepts = client.get_source_concepts(6)

    assert [item["id"] for item in concepts] == [
        "Country",
        "Series",
        "Counterpart-Area",
        "Time",
    ]
    assert concepts[0]["source"]["id"] == "6"


def test_get_source_variables_parses_series_catalog() -> None:
    payload = {
        "page": 1,
        "pages": 1,
        "per_page": 1000,
        "total": 1,
        "source": [
            {
                "id": "6",
                "name": "International Debt Statistics",
                "concept": [
                    {
                        "id": "Series",
                        "name": "series",
                        "variable": [
                            {"id": "DT.DOD.DECT.CD", "value": "External debt stocks, total"}
                        ],
                    }
                ],
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/sources/6/series/data"
        return httpx.Response(200, json=payload)

    client = make_client(handler)
    series = client.get_source_variables(6, "Series")

    assert series[0]["id"] == "DT.DOD.DECT.CD"
    assert series[0]["source"]["value"] == "International Debt Statistics"


def test_get_advanced_data_builds_multidimensional_request() -> None:
    payload = {
        "page": 1,
        "pages": 1,
        "per_page": 1000,
        "total": 1,
        "source": {
            "id": "6",
            "name": "International Debt Statistics",
            "data": [
                {
                    "variable": [
                        {"concept": "Country", "id": "ARG", "value": "Argentina"},
                        {
                            "concept": "Series",
                            "id": "DT.DOD.DECT.CD",
                            "value": "External debt stocks, total",
                        },
                        {
                            "concept": "Counterpart-Area",
                            "id": "WLD",
                            "value": "World",
                        },
                        {"concept": "Time", "id": "YR2023", "value": "2023"},
                    ],
                    "value": 123.0,
                }
            ],
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == (
            "/v2/sources/6/country/ARG/series/DT.DOD.DECT.CD/counterpart-area/WLD/time/YR2023/data"
        )
        return httpx.Response(200, json=payload)

    client = make_client(handler)
    records = client.get_advanced_data(
        source_id=6,
        dimensions={
            "Country": ["ARG"],
            "Series": ["DT.DOD.DECT.CD"],
            "Counterpart-Area": ["WLD"],
            "Time": ["YR2023"],
        },
    )

    assert len(records) == 1
    assert records[0]["value"] == 123.0
