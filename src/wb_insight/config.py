"""Application and research configuration models."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Self, TypeVar, cast

import yaml
from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

EnvironmentName = Literal["local", "test", "dev", "prod"]
LogLevelName = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
MissingValuePolicy = Literal["keep_null", "drop", "impute"]
IndicatorRole = Literal["target", "feature", "context"]
ModelT = TypeVar("ModelT", bound=BaseModel)


class ConfigurationError(RuntimeError):
    """Raised when an application configuration file cannot be loaded."""


class AppSettings(BaseSettings):
    """Runtime settings loaded from environment variables and an optional .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        validate_default=True,
    )

    app_env: EnvironmentName = "local"
    log_level: LogLevelName = "INFO"
    wb_api_base_url: AnyHttpUrl = AnyHttpUrl("https://api.worldbank.org/v2")
    wb_api_timeout_seconds: float = Field(default=30.0, gt=0)
    wb_api_per_page: int = Field(default=1000, ge=1, le=20000)
    wb_api_max_attempts: int = Field(default=3, ge=1, le=10)
    wb_api_retry_wait_seconds: float = Field(default=0.5, ge=0, le=60)
    raw_data_dir: Path = Path("data/raw")
    processed_data_dir: Path = Path("data/processed")
    research_config_path: Path = Path("configs/research.yaml")
    indicators_config_path: Path = Path("configs/indicators.yaml")
    country_groups_config_path: Path = Path("configs/country_groups.yaml")

    clickhouse_host: str | None = None
    clickhouse_port: int = Field(default=8123, ge=1, le=65535)
    clickhouse_database: str = "wb_insight"
    clickhouse_user: str | None = None
    clickhouse_password: str | None = None

    s3_endpoint: str | None = None
    s3_bucket: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None

    yandex_folder_id: str | None = None
    yandex_model_uri: str | None = None

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        """Allow lower-case log levels in environment files."""

        return value.upper() if isinstance(value, str) else value


class StrictConfigModel(BaseModel):
    """Base model for YAML files: unknown fields are treated as errors."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ProjectSection(StrictConfigModel):
    """Project metadata and the default research period."""

    name: str = Field(min_length=1)
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    start_year: int = Field(ge=1960, le=2100)
    end_year: int = Field(ge=1960, le=2100)

    @model_validator(mode="after")
    def validate_year_range(self) -> Self:
        """Ensure that the configured period is ordered correctly."""

        if self.end_year < self.start_year:
            raise ValueError("end_year must be greater than or equal to start_year")
        return self


class ResearchSection(StrictConfigModel):
    """Research hypothesis represented as a target metric and analytical questions."""

    target_indicator: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    questions: list[str] = Field(min_length=1)

    @field_validator("questions")
    @classmethod
    def normalize_questions(cls, values: list[str]) -> list[str]:
        """Reject blank and duplicate analytical questions."""

        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("research questions cannot be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("research questions must be unique")
        return normalized


class ScopeSection(StrictConfigModel):
    """Countries and grouping dimensions included in the first project scope."""

    countries: list[str] = Field(min_length=1)
    comparison_groups: list[str] = Field(min_length=1)

    @field_validator("countries")
    @classmethod
    def normalize_country_codes(cls, values: list[str]) -> list[str]:
        """Normalize and validate ISO3-like country codes."""

        normalized = [value.strip().upper() for value in values]
        invalid = [value for value in normalized if len(value) != 3 or not value.isalpha()]
        if invalid:
            raise ValueError(f"invalid country codes: {', '.join(invalid)}")
        if len(normalized) != len(set(normalized)):
            raise ValueError("scope country codes must be unique")
        return normalized

    @field_validator("comparison_groups")
    @classmethod
    def normalize_comparison_groups(cls, values: list[str]) -> list[str]:
        """Normalize grouping dimension names and reject duplicates."""

        normalized = [value.strip().lower() for value in values]
        if any(not value for value in normalized):
            raise ValueError("comparison group names cannot be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("comparison group names must be unique")
        return normalized


class DataPolicySection(StrictConfigModel):
    """Rules that prevent silent distortion of analytical data."""

    exclude_aggregates: bool = True
    missing_values: MissingValuePolicy = "keep_null"
    minimum_observations_for_correlation: int = Field(default=20, ge=3)
    require_common_year_for_rankings: bool = True


class ResearchConfig(StrictConfigModel):
    """Top-level schema of configs/research.yaml."""

    project: ProjectSection
    research: ResearchSection
    scope: ScopeSection
    data_policy: DataPolicySection


class IndicatorSpec(StrictConfigModel):
    """Metadata for one World Bank indicator used by the project."""

    code: str = Field(pattern=r"^[A-Z0-9][A-Z0-9.]*$")
    alias: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name_ru: str = Field(min_length=1)
    category: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    role: IndicatorRole
    enabled: bool = True
    unit_hint: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")


class IndicatorRegistry(StrictConfigModel):
    """Top-level schema of configs/indicators.yaml."""

    source_id: int = Field(default=2, gt=0)
    indicators: list[IndicatorSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_registry(self) -> Self:
        """Require unique codes and aliases and one enabled target indicator."""

        codes = [indicator.code for indicator in self.indicators]
        aliases = [indicator.alias for indicator in self.indicators]
        if len(codes) != len(set(codes)):
            raise ValueError("indicator codes must be unique")
        if len(aliases) != len(set(aliases)):
            raise ValueError("indicator aliases must be unique")

        enabled_targets = [
            indicator
            for indicator in self.indicators
            if indicator.enabled and indicator.role == "target"
        ]
        if len(enabled_targets) != 1:
            raise ValueError("exactly one enabled indicator must have role='target'")
        return self

    def enabled_indicators(self) -> list[IndicatorSpec]:
        """Return indicators included in the current project scope."""

        return [indicator for indicator in self.indicators if indicator.enabled]


class CountryGroupSpec(StrictConfigModel):
    """One manually curated group of countries."""

    name_ru: str = Field(min_length=1)
    description: str | None = None
    countries: list[str] = Field(min_length=1)

    @field_validator("countries")
    @classmethod
    def normalize_country_codes(cls, values: list[str]) -> list[str]:
        """Normalize and validate countries in a custom group."""

        normalized = [value.strip().upper() for value in values]
        invalid = [value for value in normalized if len(value) != 3 or not value.isalpha()]
        if invalid:
            raise ValueError(f"invalid country codes: {', '.join(invalid)}")
        if len(normalized) != len(set(normalized)):
            raise ValueError("country codes inside a group must be unique")
        return normalized


class CountryGroupRegistry(StrictConfigModel):
    """Top-level schema of configs/country_groups.yaml."""

    groups: dict[str, CountryGroupSpec] = Field(min_length=1)

    @field_validator("groups")
    @classmethod
    def validate_group_keys(
        cls, values: dict[str, CountryGroupSpec]
    ) -> dict[str, CountryGroupSpec]:
        """Require snake_case identifiers for custom groups."""

        invalid = [
            key
            for key in values
            if not key or not key[0].islower() or not key.replace("_", "").isalnum()
        ]
        if invalid:
            raise ValueError(f"invalid group identifiers: {', '.join(invalid)}")
        return values


class ApplicationConfig(StrictConfigModel):
    """Validated project configuration assembled from all local sources."""

    settings: AppSettings
    research: ResearchConfig
    indicators: IndicatorRegistry
    country_groups: CountryGroupRegistry


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    """Read a UTF-8 YAML file and require a mapping at its root."""

    try:
        with path.open("r", encoding="utf-8") as stream:
            payload = yaml.safe_load(stream)
    except FileNotFoundError as exc:
        raise ConfigurationError(f"configuration file not found: {path}") from exc
    except OSError as exc:
        raise ConfigurationError(f"cannot read configuration file {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"invalid YAML in {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ConfigurationError(f"configuration root must be a mapping: {path}")
    return cast(dict[str, Any], payload)


def _validate_yaml_model[ModelT: BaseModel](
    path: Path,
    model_type: type[ModelT],
) -> ModelT:
    """Load a YAML mapping and validate it with the supplied Pydantic model."""

    payload = _read_yaml_mapping(path)
    try:
        return model_type.model_validate(payload)
    except ValidationError as exc:
        raise ConfigurationError(f"invalid configuration in {path}:\n{exc}") from exc


def load_research_config(path: str | Path) -> ResearchConfig:
    """Load and validate the research configuration."""

    return _validate_yaml_model(Path(path), ResearchConfig)


def load_indicator_registry(path: str | Path) -> IndicatorRegistry:
    """Load and validate the indicator registry."""

    return _validate_yaml_model(Path(path), IndicatorRegistry)


def load_country_group_registry(path: str | Path) -> CountryGroupRegistry:
    """Load and validate custom country groups."""

    return _validate_yaml_model(Path(path), CountryGroupRegistry)


def load_application_config(settings: AppSettings | None = None) -> ApplicationConfig:
    """Load all project configuration files and validate cross-file references."""

    resolved_settings = settings or get_settings()
    research = load_research_config(resolved_settings.research_config_path)
    indicators = load_indicator_registry(resolved_settings.indicators_config_path)
    country_groups = load_country_group_registry(resolved_settings.country_groups_config_path)

    target_alias = research.research.target_indicator
    matched_target = next(
        (indicator for indicator in indicators.indicators if indicator.alias == target_alias),
        None,
    )
    if matched_target is None:
        raise ConfigurationError(
            f"target indicator '{target_alias}' is absent from the indicator registry"
        )
    if not matched_target.enabled or matched_target.role != "target":
        raise ConfigurationError(
            f"target indicator '{target_alias}' must be enabled and have role='target'"
        )

    unknown_scope_countries = sorted(
        set(research.scope.countries)
        - {country for group in country_groups.groups.values() for country in group.countries}
    )
    if unknown_scope_countries:
        joined = ", ".join(unknown_scope_countries)
        raise ConfigurationError(
            "all scope countries must belong to at least one custom country group; "
            f"missing: {joined}"
        )

    return ApplicationConfig(
        settings=resolved_settings,
        research=research,
        indicators=indicators,
        country_groups=country_groups,
    )


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return a cached runtime settings object."""

    return AppSettings()
