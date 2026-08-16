from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from wb_insight.config import (
    AppSettings,
    ConfigurationError,
    IndicatorRegistry,
    get_settings,
    load_application_config,
    load_indicator_registry,
    load_research_config,
)

ROOT = Path(__file__).resolve().parents[1]


def test_repository_configuration_is_valid() -> None:
    settings = AppSettings(
        _env_file=None,
        research_config_path=ROOT / "configs/research.yaml",
        indicators_config_path=ROOT / "configs/indicators.yaml",
        country_groups_config_path=ROOT / "configs/country_groups.yaml",
        raw_data_dir=ROOT / "data/raw",
        processed_data_dir=ROOT / "data/processed",
    )

    config = load_application_config(settings)

    assert config.research.project.start_year == 2000
    assert config.research.project.end_year == 2024
    assert config.research.research.target_indicator == "gdp_per_capita"
    assert len(config.research.scope.countries) == 6
    assert len(config.indicators.enabled_indicators()) == 7


def test_log_level_is_normalized_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "debug")
    settings = AppSettings(_env_file=None)

    assert settings.log_level == "DEBUG"


def test_get_settings_uses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("APP_ENV", "test")

    first = get_settings()
    second = get_settings()

    assert first is second
    assert first.app_env == "test"
    get_settings.cache_clear()


def test_research_loader_rejects_invalid_year_range(tmp_path: Path) -> None:
    path = tmp_path / "research.yaml"
    path.write_text(
        """
project:
  name: Invalid
  slug: invalid
  start_year: 2024
  end_year: 2000
research:
  target_indicator: gdp_per_capita
  questions:
    - Test question
scope:
  countries: [LVA]
  comparison_groups: [region]
data_policy:
  exclude_aggregates: true
  missing_values: keep_null
  minimum_observations_for_correlation: 20
  require_common_year_for_rankings: true
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="end_year"):
        load_research_config(path)


def test_indicator_registry_rejects_duplicate_aliases() -> None:
    payload = {
        "indicators": [
            {
                "code": "NY.GDP.PCAP.CD",
                "alias": "gdp_per_capita",
                "name_ru": "ВВП",
                "category": "economy",
                "role": "target",
                "enabled": True,
            },
            {
                "code": "SP.POP.TOTL",
                "alias": "gdp_per_capita",
                "name_ru": "Население",
                "category": "demographics",
                "role": "context",
                "enabled": True,
            },
        ]
    }

    with pytest.raises(ValidationError, match="aliases must be unique"):
        IndicatorRegistry.model_validate(payload)


def test_indicator_loader_reports_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not found"):
        load_indicator_registry(tmp_path / "missing.yaml")


def test_application_config_rejects_unknown_target(tmp_path: Path) -> None:
    research_path = tmp_path / "research.yaml"
    indicators_path = tmp_path / "indicators.yaml"
    groups_path = tmp_path / "groups.yaml"

    research_path.write_text(
        """
project:
  name: Test
  slug: test
  start_year: 2000
  end_year: 2024
research:
  target_indicator: missing_target
  questions: [Question]
scope:
  countries: [LVA]
  comparison_groups: [custom]
data_policy:
  exclude_aggregates: true
  missing_values: keep_null
  minimum_observations_for_correlation: 20
  require_common_year_for_rankings: true
""".strip(),
        encoding="utf-8",
    )
    indicators_path.write_text(
        """
indicators:
  - code: NY.GDP.PCAP.CD
    alias: gdp_per_capita
    name_ru: GDP
    category: economy
    role: target
    enabled: true
""".strip(),
        encoding="utf-8",
    )
    groups_path.write_text(
        """
groups:
  pilot:
    name_ru: Pilot
    countries: [LVA]
""".strip(),
        encoding="utf-8",
    )
    settings = AppSettings(
        _env_file=None,
        research_config_path=research_path,
        indicators_config_path=indicators_path,
        country_groups_config_path=groups_path,
    )

    with pytest.raises(ConfigurationError, match="absent from the indicator registry"):
        load_application_config(settings)


def test_repository_indicator_registry_contains_curated_units() -> None:
    registry = load_indicator_registry(ROOT / "configs/indicators.yaml")
    by_alias = {indicator.alias: indicator for indicator in registry.indicators}

    assert by_alias["gdp_per_capita"].unit == "current_usd_per_person"
    assert by_alias["gdp_per_capita"].display_unit == "US$ / person"
    assert by_alias["co2_emissions"].code == "EN.GHG.CO2.MT.CE.AR5"
    assert by_alias["co2_emissions"].unit == "mt_co2e"


def test_indicator_registry_supports_per_indicator_source_override() -> None:
    registry = IndicatorRegistry.model_validate(
        {
            "source_id": 2,
            "indicators": [
                {
                    "code": "NY.GDP.PCAP.CD",
                    "alias": "gdp_per_capita",
                    "name_ru": "GDP",
                    "category": "economy",
                    "role": "target",
                    "enabled": True,
                },
                {
                    "code": "DT.DOD.DECT.CD",
                    "alias": "external_debt",
                    "name_ru": "External debt",
                    "category": "debt",
                    "role": "feature",
                    "enabled": True,
                    "source_id": 6,
                },
            ],
        }
    )

    assert registry.effective_source_id(registry.indicators[0]) == 2
    assert registry.effective_source_id(registry.indicators[1]) == 6


def test_indicator_registry_allows_same_code_in_different_sources() -> None:
    registry = IndicatorRegistry.model_validate(
        {
            "source_id": 2,
            "indicators": [
                {
                    "code": "DUP.CODE",
                    "alias": "dup_wdi",
                    "name_ru": "WDI",
                    "category": "economy",
                    "role": "target",
                    "enabled": True,
                },
                {
                    "code": "DUP.CODE",
                    "alias": "dup_ids",
                    "name_ru": "IDS",
                    "category": "debt",
                    "role": "feature",
                    "enabled": True,
                    "source_id": 6,
                },
            ],
        }
    )

    assert len(registry.indicators) == 2


def test_indicator_registry_accepts_underscore_in_world_bank_code() -> None:
    registry = IndicatorRegistry.model_validate(
        {
            "source_id": 2,
            "indicators": [
                {
                    "code": "NY.GDP.PCAP.CD",
                    "alias": "gdp_per_capita",
                    "name_ru": "GDP per capita",
                    "category": "economy",
                    "role": "target",
                    "enabled": True,
                },
                {
                    "code": "GOV_WGI_CC.EST",
                    "alias": "control_of_corruption_estimate",
                    "name_ru": "Control of Corruption",
                    "category": "governance",
                    "role": "feature",
                    "enabled": False,
                    "source_id": 3,
                },
            ],
        }
    )

    governance = registry.indicators[1]
    assert governance.code == "GOV_WGI_CC.EST"
    assert registry.effective_source_id(governance) == 3
