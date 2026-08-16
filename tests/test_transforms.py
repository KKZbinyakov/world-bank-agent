from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from wb_insight.config import IndicatorRegistry, load_indicator_registry
from wb_insight.transforms import (
    enrich_observations_with_indicator_semantics,
    normalize_advanced_observations,
    normalize_countries,
    normalize_indicators,
    normalize_observations,
)

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[1]
LOADED_AT = datetime(2026, 8, 15, 10, 0, tzinfo=UTC)


def _fixture_records(name: str) -> list[dict[str, Any]]:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return payload[1]


def test_normalize_countries_marks_aggregates_and_numeric_coordinates() -> None:
    records = _fixture_records("countries_page_1.json")
    records[0]["longitude"] = "13.405"
    records[0]["latitude"] = "52.52"
    records.append(
        {
            "id": "WLD",
            "iso2Code": "1W",
            "name": "World",
            "region": {"id": "NA", "value": "Aggregates"},
        }
    )

    frame = normalize_countries(records, run_id="run-1", loaded_at=LOADED_AT)

    germany = frame.loc[frame["country_code"] == "DEU"].iloc[0]
    world = frame.loc[frame["country_code"] == "WLD"].iloc[0]
    assert germany["longitude"] == 13.405
    assert germany["latitude"] == 52.52
    assert bool(germany["is_aggregate"]) is False
    assert bool(world["is_aggregate"]) is True
    assert set(frame["run_id"]) == {"run-1"}


def test_normalize_indicators_applies_registry_semantics() -> None:
    records = _fixture_records("indicators_page_1.json")
    records[0]["topics"] = [
        {"id": "3", "value": "Economy & Growth"},
        {"id": "8", "value": "Infrastructure"},
    ]
    registry = load_indicator_registry(ROOT / "configs/indicators.yaml")

    frame = normalize_indicators(
        records,
        run_id="run-2",
        loaded_at=LOADED_AT,
        registry=registry,
    )

    gdp = frame.loc[frame["indicator_code"] == "NY.GDP.PCAP.CD"].iloc[0]
    assert int(gdp["source_id"]) == 2
    assert gdp["source_name"] == "World Development Indicators"
    assert gdp["topic_ids"] == "3|8"
    assert gdp["topic_names"] == "Economy & Growth|Infrastructure"
    assert gdp["alias"] == "gdp_per_capita"
    assert gdp["category"] == "economy"
    assert gdp["unit"] == "current_usd_per_person"
    assert gdp["display_unit"] == "US$ / person"
    assert gdp["unit_source"] == "registry"
    assert bool(gdp["is_registered"]) is True


def test_unregistered_indicator_uses_source_metadata_without_guessing_unit() -> None:
    registry = IndicatorRegistry.model_validate(
        {
            "source_id": 2,
            "indicators": [
                {
                    "code": "NY.GDP.PCAP.CD",
                    "alias": "gdp_per_capita",
                    "name_ru": "ВВП на душу населения",
                    "category": "economy",
                    "role": "target",
                    "enabled": True,
                    "unit": "current_usd_per_person",
                    "display_unit": "US$ / person",
                }
            ],
        }
    )

    records = [
        {
            "id": "SL.UEM.TOTL.ZS",
            "name": "Unemployment, total (% of total labor force)",
            "unit": "",
            "source": {
                "id": "2",
                "value": "World Development Indicators",
            },
            "topics": [
                {
                    "id": "10",
                    "value": "Social Protection & Labor",
                }
            ],
        }
    ]

    frame = normalize_indicators(
        records,
        run_id="run-discovered",
        loaded_at=LOADED_AT,
        registry=registry,
    )

    indicator = frame.iloc[0]

    assert bool(indicator["is_registered"]) is False
    assert pd.isna(indicator["alias"])
    assert pd.isna(indicator["unit"])
    assert pd.isna(indicator["display_unit"])
    assert indicator["unit_source"] == "missing"
    assert indicator["category"] == "social_protection_labor"
    assert indicator["category_source"] == "world_bank_topic"


def test_normalize_observations_keeps_missing_values_as_null() -> None:
    records = _fixture_records("observations_page_1.json")
    records[0]["value"] = None

    frame = normalize_observations(
        records,
        source_id=2,
        run_id="run-3",
        loaded_at=LOADED_AT,
    )

    assert len(frame) == 4
    assert frame["year"].dtype.name == "Int64"
    assert frame["value"].dtype.name == "Float64"
    assert frame["is_missing"].sum() == 1
    assert set(frame["dimensions_json"]) == {"{}"}
    assert set(frame["dimension_count"]) == {0}
    missing_row = frame.loc[
        (frame["country_code"] == "DEU") & (frame["indicator_code"] == "NY.GDP.PCAP.CD")
    ].iloc[0]
    assert bool(missing_row["is_missing"]) is True


def test_observations_receive_registry_units_and_categories() -> None:
    registry = load_indicator_registry(ROOT / "configs/indicators.yaml")
    indicator_frame = normalize_indicators(
        _fixture_records("indicators_page_1.json"),
        run_id="run-4",
        loaded_at=LOADED_AT,
        registry=registry,
    )
    observation_frame = normalize_observations(
        _fixture_records("observations_page_1.json"),
        source_id=2,
        run_id="run-4",
        loaded_at=LOADED_AT,
    )

    enriched = enrich_observations_with_indicator_semantics(
        observation_frame,
        indicator_frame,
    )

    gdp = enriched.loc[enriched["indicator_code"] == "NY.GDP.PCAP.CD"].iloc[0]
    population = enriched.loc[enriched["indicator_code"] == "SP.POP.TOTL"].iloc[0]
    assert gdp["indicator_alias"] == "gdp_per_capita"
    assert gdp["indicator_category"] == "economy"
    assert gdp["unit"] == "current_usd_per_person"
    assert population["unit"] == "people"


def test_registry_semantics_are_keyed_by_source_and_indicator_code() -> None:
    from wb_insight.config import IndicatorRegistry

    registry = IndicatorRegistry.model_validate(
        {
            "source_id": 2,
            "indicators": [
                {
                    "code": "DUP.CODE",
                    "alias": "dup_wdi",
                    "name_ru": "WDI duplicate",
                    "category": "economy",
                    "role": "target",
                    "enabled": True,
                    "unit": "usd",
                },
                {
                    "code": "DUP.CODE",
                    "alias": "dup_ids",
                    "name_ru": "IDS duplicate",
                    "category": "debt",
                    "role": "feature",
                    "enabled": True,
                    "unit": "debt_usd",
                    "source_id": 6,
                },
            ],
        }
    )
    records = [
        {
            "id": "DUP.CODE",
            "name": "Source 2 series",
            "unit": "",
            "source": {"id": "2", "value": "World Development Indicators"},
            "topics": [],
        },
        {
            "id": "DUP.CODE",
            "name": "Source 6 series",
            "unit": "",
            "source": {"id": "6", "value": "International Debt Statistics"},
            "topics": [],
        },
    ]

    frame = normalize_indicators(
        records,
        run_id="multi-source-semantics",
        loaded_at=LOADED_AT,
        registry=registry,
    )

    source_2 = frame.loc[frame["source_id"] == 2].iloc[0]
    source_6 = frame.loc[frame["source_id"] == 6].iloc[0]
    assert source_2["alias"] == "dup_wdi"
    assert source_2["unit"] == "usd"
    assert source_6["alias"] == "dup_ids"
    assert source_6["unit"] == "debt_usd"


def test_normalize_advanced_observations_preserves_extra_dimensions() -> None:
    records = [
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
    ]

    frame = normalize_advanced_observations(
        records,
        source_id=6,
        run_id="advanced-run",
        loaded_at=LOADED_AT,
    )

    row = frame.iloc[0]
    assert int(row["source_id"]) == 6
    assert row["country_code"] == "ARG"
    assert row["indicator_code"] == "DT.DOD.DECT.CD"
    assert int(row["year"]) == 2023
    assert int(row["dimension_count"]) == 1
    dimensions = json.loads(row["dimensions_json"])
    assert dimensions["Counterpart-Area"]["id"] == "WLD"
