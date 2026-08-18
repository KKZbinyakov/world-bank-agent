from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from wb_insight.analytics import (
    AnalyticalRepository,
    calculate_correlation,
    calculate_trend,
    compare_countries,
)
from wb_insight.config import AppSettings
from wb_insight.storage import ClickHouseRepository

ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "ci_clickhouse_integration_v1"
LOADED_AT = pd.Timestamp("2026-08-18T00:00:00Z")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_CLICKHOUSE_INTEGRATION") != "1",
        reason="set RUN_CLICKHOUSE_INTEGRATION=1 to run live ClickHouse tests",
    ),
]


def _settings() -> AppSettings:
    settings = AppSettings(_env_file=None)
    if settings.clickhouse_database not in {"wb_insight_ci", "wb_insight_test"}:
        pytest.fail(
            "ClickHouse integration tests require a dedicated database named "
            "wb_insight_ci or wb_insight_test"
        )
    return settings


def _countries() -> pd.DataFrame:
    rows = [
        {
            "country_code": "DEU",
            "iso2_code": "DE",
            "country_name": "Germany",
            "region_code": "ECS",
            "region_name": "Europe & Central Asia",
            "admin_region_code": "",
            "admin_region_name": "",
            "income_level_code": "HIC",
            "income_level_name": "High income",
            "lending_type_code": "LNX",
            "lending_type_name": "Not classified",
            "capital_city": "Berlin",
            "longitude": 13.4,
            "latitude": 52.5,
            "is_aggregate": False,
            "run_id": RUN_ID,
            "loaded_at": LOADED_AT,
        },
        {
            "country_code": "NLD",
            "iso2_code": "NL",
            "country_name": "Netherlands",
            "region_code": "ECS",
            "region_name": "Europe & Central Asia",
            "admin_region_code": "",
            "admin_region_name": "",
            "income_level_code": "HIC",
            "income_level_name": "High income",
            "lending_type_code": "LNX",
            "lending_type_name": "Not classified",
            "capital_city": "Amsterdam",
            "longitude": 4.9,
            "latitude": 52.37,
            "is_aggregate": False,
            "run_id": RUN_ID,
            "loaded_at": LOADED_AT,
        },
    ]
    return pd.DataFrame(rows)


def _indicator_rows() -> list[dict[str, Any]]:
    return [
        {
            "source_id": 2,
            "source_name": "World Development Indicators",
            "indicator_code": "NY.GDP.PCAP.CD",
            "indicator_name": "GDP per capita (current US$)",
            "source_unit": None,
            "alias": "gdp_per_capita_current_usd",
            "name_ru": "ВВП на душу населения",
            "category": "economy",
            "category_source": "registry",
            "role": "target",
            "unit": "current_usd_per_person",
            "display_unit": "US$ / person",
            "unit_source": "registry",
            "is_registered": True,
            "source_note": "",
            "source_organization": "World Bank",
            "topic_ids": "3",
            "topic_names": "Economy & Growth",
            "run_id": RUN_ID,
            "loaded_at": LOADED_AT,
        },
        {
            "source_id": 2,
            "source_name": "World Development Indicators",
            "indicator_code": "SL.UEM.TOTL.ZS",
            "indicator_name": "Unemployment, total (% of total labor force)",
            "source_unit": None,
            "alias": "unemployment_pct_labor_force",
            "name_ru": "Безработица",
            "category": "labor",
            "category_source": "registry",
            "role": "feature",
            "unit": "percent_of_labor_force",
            "display_unit": "%",
            "unit_source": "registry",
            "is_registered": True,
            "source_note": "",
            "source_organization": "ILO",
            "topic_ids": "10",
            "topic_names": "Social Protection & Labor",
            "run_id": RUN_ID,
            "loaded_at": LOADED_AT,
        },
    ]


def _indicators() -> pd.DataFrame:
    return pd.DataFrame(_indicator_rows())


def _observations() -> pd.DataFrame:
    country_names = {"DEU": "Germany", "NLD": "Netherlands"}
    values: dict[tuple[str, str, int], float | None] = {
        ("DEU", "NY.GDP.PCAP.CD", 2023): 50_000.0,
        ("DEU", "NY.GDP.PCAP.CD", 2024): 52_000.0,
        ("NLD", "NY.GDP.PCAP.CD", 2023): 60_000.0,
        ("NLD", "NY.GDP.PCAP.CD", 2024): 62_000.0,
        ("DEU", "SL.UEM.TOTL.ZS", 2023): 3.0,
        ("DEU", "SL.UEM.TOTL.ZS", 2024): 3.2,
        ("NLD", "SL.UEM.TOTL.ZS", 2023): None,
        ("NLD", "SL.UEM.TOTL.ZS", 2024): 3.5,
    }
    metadata = {row["indicator_code"]: row for row in _indicator_rows()}
    rows: list[dict[str, Any]] = []
    for (country_code, indicator_code, year), value in values.items():
        indicator = metadata[indicator_code]
        rows.append(
            {
                "source_id": 2,
                "indicator_code": indicator_code,
                "indicator_name": indicator["indicator_name"],
                "indicator_alias": indicator["alias"],
                "indicator_name_ru": indicator["name_ru"],
                "indicator_category": indicator["category"],
                "category_source": "registry",
                "indicator_role": indicator["role"],
                "country_code": country_code,
                "country_name": country_names[country_code],
                "year": year,
                "value": value,
                "dimensions_json": "{}",
                "dimension_count": 0,
                "source_unit": None,
                "unit": indicator["unit"],
                "display_unit": indicator["display_unit"],
                "unit_source": "registry",
                "is_registered": True,
                "observation_status": "",
                "decimal_scale": 2,
                "is_missing": value is None,
                "run_id": RUN_ID,
                "loaded_at": LOADED_AT,
            }
        )
    return pd.DataFrame(rows)


def _write_fixture(tmp_path: Path) -> tuple[Path, Path]:
    run_dir = tmp_path / f"run_id={RUN_ID}"
    run_dir.mkdir()
    _countries().to_parquet(run_dir / "countries.parquet", index=False)
    _indicators().to_parquet(run_dir / "indicators.parquet", index=False)
    _observations().to_parquet(run_dir / "observations.parquet", index=False)

    mart_dir = tmp_path / "marts"
    mart_dir.mkdir()
    pd.DataFrame(
        [
            {
                "country_code": country_code,
                "country_name": country_name,
                "region_name": "Europe & Central Asia",
                "income_level_name": "High income",
                "longitude": longitude,
                "latitude": latitude,
                "year": year,
                "gdp_per_capita_current_usd": gdp,
                "unemployment_pct_labor_force": unemployment,
            }
            for country_code, country_name, longitude, latitude, year, gdp, unemployment in [
                ("DEU", "Germany", 13.4, 52.5, 2023, 50_000.0, 3.0),
                ("DEU", "Germany", 13.4, 52.5, 2024, 52_000.0, 3.2),
                ("NLD", "Netherlands", 4.9, 52.37, 2023, 60_000.0, None),
                ("NLD", "Netherlands", 4.9, 52.37, 2024, 62_000.0, 3.5),
            ]
        ]
    ).to_csv(mart_dir / "worldbank_datalens_wide.csv", index=False)
    pd.DataFrame(
        [
            {
                "source_id": 2,
                "indicator_code": "NY.GDP.PCAP.CD",
                "indicator_name": "GDP per capita (current US$)",
                "wide_column": "gdp_per_capita_current_usd",
                "category": "economy",
                "unit": "current_usd_per_person",
                "display_unit": "US$ / person",
            },
            {
                "source_id": 2,
                "indicator_code": "SL.UEM.TOTL.ZS",
                "indicator_name": "Unemployment, total (% of total labor force)",
                "wide_column": "unemployment_pct_labor_force",
                "category": "labor",
                "unit": "percent_of_labor_force",
                "display_unit": "%",
            },
        ]
    ).to_csv(mart_dir / "worldbank_metric_catalog.csv", index=False)
    return run_dir, mart_dir


def test_processed_run_loads_into_live_clickhouse(tmp_path: Path) -> None:
    run_dir, mart_dir = _write_fixture(tmp_path)

    with ClickHouseRepository.from_settings(_settings()) as repository:
        assert repository.apply_sql_directory(ROOT / "sql" / "ddl")
        assert repository.apply_sql_directory(ROOT / "sql" / "marts")

        result = repository.load_processed_run(run_dir, mart_dir=mart_dir, batch_size=2)
        assert result.run_id == RUN_ID
        assert result.countries == 2
        assert result.indicators == 2
        assert result.observations == 8
        assert result.wide_rows == 4
        assert result.metric_catalog_rows == 2

        assert (
            repository.scalar(
                f"SELECT count() FROM etl_run WHERE run_id = '{RUN_ID}' AND status = 'loaded'"
            )
            == 1
        )
        assert (
            repository.scalar(
                f"SELECT count() FROM mart_indicator_timeseries WHERE run_id = '{RUN_ID}'"
            )
            == 8
        )
        assert (
            repository.scalar(f"SELECT count() FROM mart_country_year WHERE run_id = '{RUN_ID}'")
            == 8
        )
        assert repository.scalar("SELECT count() FROM mart_country_year_wide") == 4
        assert repository.scalar("SELECT count() FROM mart_metric_catalog") == 2
        assert repository.scalar("SELECT count() FROM mart_country_snapshot") == 4
        assert (
            repository.scalar(
                "SELECT count() FROM mart_country_snapshot WHERE observation_year = 2024"
            )
            == 4
        )
        assert repository.scalar("SELECT count() FROM mart_data_quality") == 4
        assert (
            repository.scalar("SELECT count() FROM mart_data_quality WHERE coverage_ratio < 1") == 1
        )
        assert repository.scalar(
            "SELECT value FROM mart_country_snapshot "
            "WHERE country_code = 'DEU' "
            "AND indicator_code = 'NY.GDP.PCAP.CD'"
        ) == pytest.approx(52_000.0)

        second = repository.load_processed_run(run_dir, mart_dir=mart_dir, batch_size=3)
        assert second == result
        assert (
            repository.scalar(
                f"SELECT count() FROM etl_run WHERE run_id = '{RUN_ID}' AND status = 'loaded'"
            )
            == 1
        )
        assert (
            repository.scalar(f"SELECT count() FROM fact_observation WHERE run_id = '{RUN_ID}'")
            == 8
        )
        assert repository.scalar("SELECT count() FROM mart_country_year_wide") == 4


def test_analytical_core_queries_live_clickhouse(tmp_path: Path) -> None:
    run_dir, mart_dir = _write_fixture(tmp_path)
    with ClickHouseRepository.from_settings(_settings()) as storage:
        storage.apply_sql_directory(ROOT / "sql" / "ddl")
        storage.apply_sql_directory(ROOT / "sql" / "marts")
        storage.load_processed_run(run_dir, mart_dir=mart_dir, batch_size=2)

    with AnalyticalRepository.from_settings(_settings()) as repository:
        gdp = repository.get_timeseries(
            countries=["DEU", "NLD"],
            metrics=["2:NY.GDP.PCAP.CD"],
            start_year=2023,
            end_year=2024,
        )
        assert len(gdp.points) == 4
        assert all(item.coverage_ratio == 1 for item in gdp.coverage)

        snapshot = repository.get_country_snapshot(
            countries=["DEU", "NLD"],
            metrics=["2:NY.GDP.PCAP.CD"],
            mode="common_year",
        )
        assert snapshot.comparison_year == 2024
        assert len(snapshot.points) == 2

        missing_snapshot = repository.get_country_snapshot(
            countries=["DEU", "POL"],
            metrics=["2:NY.GDP.PCAP.CD"],
            mode="common_year",
        )
        assert missing_snapshot.comparison_year is None
        assert not missing_snapshot.points
        assert len(missing_snapshot.missing_pairs) == 2

        quality = repository.get_data_quality(
            countries=["DEU", "NLD"],
            metrics=["2:SL.UEM.TOTL.ZS"],
        )
        assert len(quality.entries) == 2
        assert sum(entry.coverage_ratio < 1 for entry in quality.entries) == 1

        unemployment = repository.get_timeseries(
            countries=["DEU", "NLD"],
            metrics=["2:SL.UEM.TOTL.ZS"],
            start_year=2023,
            end_year=2024,
        )

    deu_gdp = [point for point in gdp.points if point.country_code == "DEU"]
    trend = calculate_trend(deu_gdp)
    assert trend.absolute_change == pytest.approx(2_000.0)

    comparison = compare_countries(gdp.points)
    assert comparison.year == 2024
    assert comparison.entries[0].country_code == "NLD"

    correlation = calculate_correlation(gdp.points, unemployment.points)
    assert correlation.sample_size == 3
    assert correlation.coefficient is not None
