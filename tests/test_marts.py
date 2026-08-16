from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from wb_insight.marts import MartBuildError, build_marts, load_mart_config


def _countries() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "country_code": "DEU",
                "country_name": "Germany",
                "region_name": "Europe & Central Asia",
                "income_level_name": "High income",
                "latitude": 51.0,
                "longitude": 9.0,
            },
            {
                "country_code": "NLD",
                "country_name": "Netherlands",
                "region_name": "Europe & Central Asia",
                "income_level_name": "High income",
                "latitude": 52.1,
                "longitude": 5.3,
            },
        ]
    )


def _indicators() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "source_id": 2,
                "source_name": "World Development Indicators",
                "indicator_code": "NY.GDP.PCAP.CD",
                "indicator_name": "GDP per capita",
                "alias": "gdp_per_capita",
                "category": "economy",
                "unit": "current_usd_per_person",
                "display_unit": "US$ / person",
            },
            {
                "source_id": 2,
                "source_name": "World Development Indicators",
                "indicator_code": "SL.UEM.TOTL.ZS",
                "indicator_name": "Unemployment",
                "alias": pd.NA,
                "category": "labor",
                "unit": pd.NA,
                "display_unit": pd.NA,
            },
            {
                "source_id": 6,
                "source_name": "International Debt Statistics",
                "indicator_code": "DT.DOD.DECT.CD",
                "indicator_name": "External debt stocks",
                "alias": "external_debt",
                "category": "debt",
                "unit": "usd",
                "display_unit": "US$",
            },
        ]
    )


def _observations() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for country, gdp, unemployment in (
        ("DEU", 50_000.0, 4.0),
        ("NLD", 60_000.0, 3.0),
    ):
        rows.extend(
            [
                {
                    "source_id": 2,
                    "indicator_code": "NY.GDP.PCAP.CD",
                    "country_code": country,
                    "year": 2023,
                    "value": gdp,
                    "dimensions_json": "{}",
                },
                {
                    "source_id": 2,
                    "indicator_code": "SL.UEM.TOTL.ZS",
                    "country_code": country,
                    "year": 2023,
                    "value": unemployment,
                    "dimensions_json": "{}",
                },
                {
                    "source_id": 6,
                    "indicator_code": "DT.DOD.DECT.CD",
                    "country_code": country,
                    "year": 2023,
                    "value": 100.0,
                    "dimensions_json": json.dumps(
                        {"Counterpart-Area": {"id": "WLD", "value": "World"}}
                    ),
                },
                {
                    "source_id": 6,
                    "indicator_code": "DT.DOD.DECT.CD",
                    "country_code": country,
                    "year": 2023,
                    "value": 25.0,
                    "dimensions_json": json.dumps(
                        {"Counterpart-Area": {"id": "001", "value": "Creditor"}}
                    ),
                },
            ]
        )
    return pd.DataFrame(rows)


def test_universal_builder_uses_alias_and_fallback_name() -> None:
    result = build_marts(_countries(), _indicators(), _observations())

    assert "gdp_per_capita" in result.wide.columns
    assert "s2_sl_uem_totl_zs" in result.wide.columns
    assert len(result.wide) == 2
    assert len(result.long) == 8


def test_multidimensional_values_become_separate_wide_columns() -> None:
    result = build_marts(_countries(), _indicators(), _observations())

    debt_columns = [column for column in result.wide.columns if column.startswith("external_debt")]
    assert len(debt_columns) == 2
    assert any("counterpart_area_wld" in column for column in debt_columns)
    assert any("counterpart_area_001" in column for column in debt_columns)


def test_dimension_error_mode_refuses_multidimensional_values() -> None:
    with pytest.raises(MartBuildError, match="multidimensional observations"):
        build_marts(
            _countries(),
            _indicators(),
            _observations(),
            dimension_mode="error",
        )


def test_filters_accept_alias_code_and_source_qualified_selector() -> None:
    result = build_marts(
        _countries(),
        _indicators(),
        _observations(),
        country_codes={"DEU"},
        indicator_selectors={"gdp_per_capita", "SL.UEM.TOTL.ZS"},
        start_year=2023,
        end_year=2023,
    )

    assert list(result.wide["country_code"]) == ["DEU"]
    assert "gdp_per_capita" in result.wide.columns
    assert "s2_sl_uem_totl_zs" in result.wide.columns
    assert not any(column.startswith("external_debt") for column in result.wide.columns)


def test_config_overrides_alias_country_label_and_adds_derived_metric(tmp_path: Path) -> None:
    config_path = tmp_path / "mart.yaml"
    config_path.write_text(
        """
column_aliases:
  "2:NY.GDP.PCAP.CD": gdp_usd
  "2:SL.UEM.TOTL.ZS": unemployment_pct
country_label_column: country_ru
country_labels:
  DEU: Германия
  NLD: Нидерланды
derived_metrics:
  - name: gdp_after_unemployment_proxy
    expression: "gdp_usd * (1 - unemployment_pct / 100)"
    round: 2
""".strip(),
        encoding="utf-8",
    )
    config = load_mart_config(config_path)

    result = build_marts(
        _countries(),
        _indicators(),
        _observations(),
        config=config,
        indicator_selectors={"2:NY.GDP.PCAP.CD", "2:SL.UEM.TOTL.ZS"},
    )

    germany = result.wide.loc[result.wide["country_code"] == "DEU"].iloc[0]
    assert germany["country_ru"] == "Германия"
    assert germany["gdp_usd"] == 50_000.0
    assert germany["unemployment_pct"] == 4.0
    assert germany["gdp_after_unemployment_proxy"] == 48_000.0


def test_complete_grid_adds_missing_country_year_rows() -> None:
    observations = _observations().copy()
    observations = pd.concat(
        [
            observations,
            pd.DataFrame(
                [
                    {
                        "source_id": 2,
                        "indicator_code": "NY.GDP.PCAP.CD",
                        "country_code": "DEU",
                        "year": 2022,
                        "value": 49_000.0,
                        "dimensions_json": "{}",
                    }
                ]
            ),
        ],
        ignore_index=True,
    )

    result = build_marts(_countries(), _indicators(), observations, complete_grid=True)

    assert len(result.wide) == 4
    nld_2022 = result.wide.loc[
        (result.wide["country_code"] == "NLD") & (result.wide["year"] == 2022)
    ].iloc[0]
    assert pd.isna(nld_2022["gdp_per_capita"])


def test_invalid_derived_expression_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "bad.yaml"
    config_path.write_text(
        """
derived_metrics:
  - name: bad_metric
    expression: "__import__('os').system('echo nope')"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(MartBuildError, match="unsupported syntax"):
        load_mart_config(config_path)
