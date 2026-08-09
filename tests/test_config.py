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
