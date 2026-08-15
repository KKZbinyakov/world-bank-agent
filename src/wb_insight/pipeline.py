"""Reproducible, source-aware World Bank ingestion pipeline."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

import pandas as pd

from wb_insight.config import ApplicationConfig
from wb_insight.ingestion import WorldBankAPIError, WorldBankClient
from wb_insight.quality.checks import (
    QualityCheckError,
    QualityReport,
    combine_reports,
    run_country_checks,
    run_indicator_checks,
    run_observation_checks,
)
from wb_insight.storage import RawArtifact, RawStore
from wb_insight.transforms import (
    enrich_observations_with_indicator_semantics,
    normalize_advanced_observations,
    normalize_countries,
    normalize_indicators,
    normalize_observations,
)

JsonRecord = dict[str, Any]
IndicatorKey = tuple[int, str]
DimensionFilters = Mapping[int, Mapping[str, Sequence[str]]]
SourceStrategy = Literal["classic", "advanced"]


class WorldBankReader(Protocol):
    """Subset of the API client required by the pipeline."""

    def get_countries(self) -> list[JsonRecord]: ...

    def get_indicators(self, source_id: int | None = None) -> list[JsonRecord]: ...

    def get_observations(
        self,
        *,
        indicator_codes: Sequence[str],
        country_codes: Sequence[str],
        start_year: int,
        end_year: int,
        source_id: int | None = None,
    ) -> list[JsonRecord]: ...

    def get_source_concepts(self, source_id: int) -> list[JsonRecord]: ...

    def get_source_variables(self, source_id: int, concept_id: str) -> list[JsonRecord]: ...

    def get_advanced_data(
        self,
        *,
        source_id: int,
        dimensions: Mapping[str, Sequence[str] | str],
    ) -> list[JsonRecord]: ...


@dataclass(frozen=True, slots=True)
class IndicatorSelection:
    """One source-qualified indicator selected for an ingestion run."""

    source_id: int
    code: str

    @property
    def key(self) -> IndicatorKey:
        return (self.source_id, self.code)

    @property
    def qualified_code(self) -> str:
        return f"{self.source_id}:{self.code}"


@dataclass(frozen=True, slots=True)
class SourcePlan:
    """Resolved query strategy and dimensions for one World Bank source."""

    source_id: int
    strategy: SourceStrategy
    concepts: tuple[str, ...]
    country_concept: str
    series_concept: str
    time_concept: str
    extra_dimensions: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """Paths and counters produced by one successful ingestion run."""

    run_id: str
    started_at: datetime
    finished_at: datetime
    raw_artifacts: tuple[RawArtifact, ...]
    countries_path: Path
    indicators_path: Path
    observations_path: Path
    quality_report_path: Path
    countries_count: int
    indicators_count: int
    observations_count: int
    quality_report: QualityReport


def run_ingestion(
    config: ApplicationConfig,
    *,
    country_codes: Sequence[str] | None = None,
    indicator_codes: Sequence[str] | None = None,
    dimension_filters: DimensionFilters | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
    client: WorldBankReader | None = None,
    run_id: str | None = None,
    started_at: datetime | None = None,
) -> IngestionResult:
    """Fetch, persist, normalize, validate and write one analytical snapshot.

    Registry aliases and registered codes preserve their configured source. Explicit
    ``SOURCE_ID:INDICATOR_CODE`` selectors can address any World Bank source.
    Unregistered, unqualified codes are interpreted in the registry's default source
    (normally WDI/source 2), avoiding an expensive and unreliable global catalog scan.

    Sources with only Country/Series/Time use the classic Indicators API. Sources
    with additional concepts automatically switch to the Advanced Data API. Extra
    dimensions can be supplied through ``dimension_filters``; Counterpart-Area is
    automatically set to WLD when that variable exists, which matches the standard
    total-debt interpretation in IDS.
    """

    selected_countries = _resolve_countries(config, country_codes)
    selected_indicators = _resolve_indicator_selections(config, indicator_codes)
    normalized_dimension_filters = _normalize_dimension_filters(dimension_filters)
    selected_start_year = start_year or config.research.project.start_year
    selected_end_year = end_year or config.research.project.end_year
    _validate_period(selected_start_year, selected_end_year)

    effective_run_id = run_id or _generate_run_id()
    effective_started_at = started_at or datetime.now(UTC)
    if effective_started_at.tzinfo is None:
        raise ValueError("started_at must be timezone-aware")

    raw_store = RawStore(config.settings.raw_data_dir)
    owned_client: WorldBankClient | None = None
    reader = client
    if reader is None:
        owned_client = WorldBankClient(
            base_url=str(config.settings.wb_api_base_url),
            timeout_seconds=config.settings.wb_api_timeout_seconds,
            per_page=config.settings.wb_api_per_page,
            max_attempts=config.settings.wb_api_max_attempts,
            retry_wait_seconds=config.settings.wb_api_retry_wait_seconds,
        )
        reader = owned_client

    try:
        raw_countries = reader.get_countries()
        _validate_country_availability(
            raw_countries,
            selected_countries,
            aggregates_allowed=not config.research.data_policy.exclude_aggregates,
        )
        countries_artifact = raw_store.save_records(
            dataset="countries",
            records=raw_countries,
            run_id=effective_run_id,
            request_params={"endpoint": "/country"},
            fetched_at=effective_started_at,
        )

        plans: dict[int, SourcePlan] = {}
        raw_indicators_by_source: dict[int, list[JsonRecord]] = {}
        metadata_artifacts: list[RawArtifact] = []
        selections_by_source = _group_selections_by_source(selected_indicators)

        unused_dimension_sources = sorted(
            set(normalized_dimension_filters) - set(selections_by_source)
        )
        if unused_dimension_sources:
            raise ValueError(
                "dimension filters were supplied for sources not selected by indicators: "
                + ", ".join(str(source_id) for source_id in unused_dimension_sources)
            )

        for source_id, source_selections in sorted(selections_by_source.items()):
            raw_concepts = reader.get_source_concepts(source_id)
            metadata_artifacts.append(
                raw_store.save_records(
                    dataset=f"source_{source_id}_concepts",
                    records=raw_concepts,
                    run_id=effective_run_id,
                    request_params={
                        "endpoint": f"/sources/{source_id}/concepts/data",
                        "source_id": source_id,
                    },
                    fetched_at=effective_started_at,
                )
            )
            concept_lookup = _concept_lookup(raw_concepts, source_id=source_id)
            country_concept = _required_concept(concept_lookup, "country", source_id)
            series_concept = _required_concept(concept_lookup, "series", source_id)
            time_concept = _required_concept(concept_lookup, "time", source_id)

            raw_source_countries = reader.get_source_variables(source_id, country_concept)
            _validate_source_country_availability(
                raw_source_countries,
                selected_countries,
                source_id=source_id,
            )

            raw_source_indicators = reader.get_indicators(source_id=source_id)
            _validate_source_indicator_availability(
                raw_source_indicators,
                source_selections,
                source_id=source_id,
            )
            raw_indicators_by_source[source_id] = raw_source_indicators
            metadata_artifacts.append(
                raw_store.save_records(
                    dataset=f"indicators_source_{source_id}",
                    records=raw_source_indicators,
                    run_id=effective_run_id,
                    request_params={
                        "endpoint": "/indicator",
                        "source_id": source_id,
                    },
                    fetched_at=effective_started_at,
                )
            )

            plans[source_id] = _build_source_plan(
                reader,
                source_id=source_id,
                concept_lookup=concept_lookup,
                country_concept=country_concept,
                series_concept=series_concept,
                time_concept=time_concept,
                supplied_filters=normalized_dimension_filters.get(source_id, {}),
            )

        raw_observations_by_source = _fetch_observations_by_source(
            reader,
            country_codes=selected_countries,
            selections=selected_indicators,
            plans=plans,
            start_year=selected_start_year,
            end_year=selected_end_year,
        )
        observation_artifacts = _save_observations_by_source(
            raw_store,
            records_by_source=raw_observations_by_source,
            selections=selected_indicators,
            plans=plans,
            country_codes=selected_countries,
            start_year=selected_start_year,
            end_year=selected_end_year,
            run_id=effective_run_id,
            fetched_at=effective_started_at,
        )
    finally:
        if owned_client is not None:
            owned_client.close()

    countries_frame = normalize_countries(
        raw_countries,
        run_id=effective_run_id,
        loaded_at=effective_started_at,
    )
    if config.research.data_policy.exclude_aggregates and not countries_frame.empty:
        countries_frame = countries_frame.loc[~countries_frame["is_aggregate"].fillna(False)].copy()
        countries_frame.reset_index(drop=True, inplace=True)

    raw_indicators = [
        record
        for source_id in sorted(raw_indicators_by_source)
        for record in raw_indicators_by_source[source_id]
    ]
    indicators_frame = normalize_indicators(
        raw_indicators,
        run_id=effective_run_id,
        loaded_at=effective_started_at,
        registry=config.indicators,
    )
    observations_frame = _normalize_observations_by_source(
        raw_observations_by_source,
        plans=plans,
        run_id=effective_run_id,
        loaded_at=effective_started_at,
    )
    observations_frame = enrich_observations_with_indicator_semantics(
        observations_frame,
        indicators_frame,
    )

    expected_indicator_keys = {selection.key for selection in selected_indicators}
    quality_report = combine_reports(
        run_country_checks(
            countries_frame,
            expected_country_codes=set(selected_countries),
            aggregates_allowed=not config.research.data_policy.exclude_aggregates,
        ),
        run_indicator_checks(
            indicators_frame,
            expected_indicator_keys=expected_indicator_keys,
        ),
        run_observation_checks(
            observations_frame,
            expected_country_codes=set(selected_countries),
            expected_indicator_keys=expected_indicator_keys,
            start_year=selected_start_year,
            end_year=selected_end_year,
        ),
    )

    processed_dir = config.settings.processed_data_dir / f"run_id={effective_run_id}"
    processed_dir.mkdir(parents=True, exist_ok=False)
    quality_report_path = processed_dir / "quality_report.json"
    _write_quality_report(
        quality_report_path,
        report=quality_report,
        run_id=effective_run_id,
        generated_at=datetime.now(UTC),
    )
    if quality_report.has_failures:
        raise QualityCheckError(quality_report)

    countries_path = processed_dir / "countries.parquet"
    indicators_path = processed_dir / "indicators.parquet"
    observations_path = processed_dir / "observations.parquet"
    _write_parquet(countries_frame, countries_path)
    _write_parquet(indicators_frame, indicators_path)
    _write_parquet(observations_frame, observations_path)

    return IngestionResult(
        run_id=effective_run_id,
        started_at=effective_started_at,
        finished_at=datetime.now(UTC),
        raw_artifacts=(
            countries_artifact,
            *metadata_artifacts,
            *observation_artifacts,
        ),
        countries_path=countries_path,
        indicators_path=indicators_path,
        observations_path=observations_path,
        quality_report_path=quality_report_path,
        countries_count=len(countries_frame),
        indicators_count=len(indicators_frame),
        observations_count=len(observations_frame),
        quality_report=quality_report,
    )


def _validate_country_availability(
    raw_countries: Sequence[JsonRecord],
    selected_countries: Sequence[str],
    *,
    aggregates_allowed: bool,
) -> None:
    """Fail early for unknown country codes or forbidden aggregate entities."""

    by_code = {
        str(record.get("id", "")).upper(): record for record in raw_countries if record.get("id")
    }
    missing = [code for code in selected_countries if code.upper() not in by_code]
    if missing:
        raise ValueError("World Bank does not expose selected country codes: " + ", ".join(missing))

    if aggregates_allowed:
        return

    aggregate_codes: list[str] = []
    for code in selected_countries:
        record = by_code[code.upper()]
        region = record.get("region")
        region_mapping = region if isinstance(region, dict) else {}
        region_id = str(region_mapping.get("id", "")).upper()
        region_name = str(region_mapping.get("value", "")).strip().lower()
        if region_id == "NA" or region_name in {"aggregate", "aggregates"}:
            aggregate_codes.append(code.upper())
    if aggregate_codes:
        raise ValueError(
            "selected country codes are World Bank aggregates while aggregate "
            "filtering is enabled: " + ", ".join(aggregate_codes)
        )


def _resolve_indicator_selections(
    config: ApplicationConfig,
    values: Sequence[str] | None,
) -> list[IndicatorSelection]:
    """Resolve aliases, registered codes and explicit source-qualified codes."""

    registry_by_alias = {
        indicator.alias.lower(): IndicatorSelection(
            config.indicators.effective_source_id(indicator),
            indicator.code.upper(),
        )
        for indicator in config.indicators.indicators
    }
    registry_by_code: dict[str, list[IndicatorSelection]] = defaultdict(list)
    for indicator in config.indicators.indicators:
        registry_by_code[indicator.code.upper()].append(
            IndicatorSelection(
                config.indicators.effective_source_id(indicator),
                indicator.code.upper(),
            )
        )

    if values is None:
        selections = [
            IndicatorSelection(
                config.indicators.effective_source_id(indicator),
                indicator.code.upper(),
            )
            for indicator in config.indicators.enabled_indicators()
        ]
    else:
        selections = []
        for raw_value in values:
            value = raw_value.strip()
            if not value:
                continue

            alias_match = registry_by_alias.get(value.lower())
            if alias_match is not None:
                selections.append(alias_match)
                continue

            qualified = _parse_source_qualified_indicator(value)
            if qualified is not None:
                selections.append(qualified)
                continue

            code = _normalize_indicator_code(value)
            registry_matches = registry_by_code.get(code, [])
            if len(registry_matches) == 1:
                selections.append(registry_matches[0])
                continue
            if len(registry_matches) > 1:
                sources = ", ".join(str(item.source_id) for item in registry_matches)
                raise ValueError(
                    f"indicator {code} is registered for multiple sources ({sources}); "
                    "use a registry alias or SOURCE_ID:INDICATOR_CODE"
                )

            selections.append(IndicatorSelection(config.indicators.source_id, code))

    if not selections:
        raise ValueError("at least one indicator must be selected")
    if len({selection.key for selection in selections}) != len(selections):
        raise ValueError("indicator selection resolves to duplicate (source_id, code) pairs")
    return selections


def _parse_source_qualified_indicator(value: str) -> IndicatorSelection | None:
    if ":" not in value:
        return None
    source_text, code_text = value.split(":", 1)
    if not source_text.isdigit() or int(source_text) <= 0:
        raise ValueError(
            f"invalid source-qualified indicator {value!r}; expected SOURCE_ID:INDICATOR_CODE"
        )
    return IndicatorSelection(int(source_text), _normalize_indicator_code(code_text))


def _normalize_indicator_code(value: str) -> str:
    code = value.strip().upper()
    if not code or any(character in code for character in (";", "/", " ", ":")):
        raise ValueError(f"invalid indicator code: {value}")
    return code


def _group_selections_by_source(
    selections: Sequence[IndicatorSelection],
) -> dict[int, list[IndicatorSelection]]:
    grouped: dict[int, list[IndicatorSelection]] = defaultdict(list)
    for selection in selections:
        grouped[selection.source_id].append(selection)
    return dict(grouped)


def _concept_lookup(
    raw_concepts: Sequence[JsonRecord],
    *,
    source_id: int,
) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for record in raw_concepts:
        concept_id = str(record.get("id", "")).strip()
        if not concept_id:
            continue
        key = _concept_key(concept_id)
        if key in lookup and lookup[key] != concept_id:
            raise ValueError(
                f"source {source_id} exposes ambiguous concept names for {key}: "
                f"{lookup[key]!r}, {concept_id!r}"
            )
        lookup[key] = concept_id
    if not lookup:
        raise ValueError(f"World Bank source {source_id} exposes no data concepts")
    return lookup


def _required_concept(lookup: Mapping[str, str], key: str, source_id: int) -> str:
    concept = lookup.get(key)
    if concept is None:
        raise ValueError(
            f"World Bank source {source_id} is unsupported because it has no {key!r} concept"
        )
    return concept


def _validate_source_country_availability(
    raw_countries: Sequence[JsonRecord],
    selected_countries: Sequence[str],
    *,
    source_id: int,
) -> None:
    available = {str(record.get("id", "")).strip().upper() for record in raw_countries}
    missing = sorted(set(selected_countries) - available)
    if missing:
        raise ValueError(
            f"World Bank source {source_id} does not expose selected countries: "
            + ", ".join(missing)
        )


def _validate_source_indicator_availability(
    raw_series: Sequence[JsonRecord],
    selections: Sequence[IndicatorSelection],
    *,
    source_id: int,
) -> None:
    available = {str(record.get("id", "")).strip().upper() for record in raw_series}
    missing = sorted(selection.code for selection in selections if selection.code not in available)
    if missing:
        formatted = ", ".join(f"{source_id}:{code}" for code in missing)
        raise ValueError("World Bank does not expose selected source/indicator pairs: " + formatted)


def _build_source_plan(
    reader: WorldBankReader,
    *,
    source_id: int,
    concept_lookup: Mapping[str, str],
    country_concept: str,
    series_concept: str,
    time_concept: str,
    supplied_filters: Mapping[str, tuple[str, ...]],
) -> SourcePlan:
    core_keys = {"country", "series", "time"}
    extra_concepts = [
        concept_id for key, concept_id in concept_lookup.items() if key not in core_keys
    ]
    supplied_by_key = {_concept_key(key): values for key, values in supplied_filters.items()}
    unknown_filters = sorted(set(supplied_by_key) - {_concept_key(c) for c in extra_concepts})
    if unknown_filters:
        raise ValueError(
            f"source {source_id} received filters for unknown/non-extra dimensions: "
            + ", ".join(unknown_filters)
        )

    resolved: dict[str, tuple[str, ...]] = {}
    for concept_id in extra_concepts:
        key = _concept_key(concept_id)
        raw_variables = reader.get_source_variables(source_id, concept_id)
        available = {
            str(record.get("id", "")).strip(): str(record.get("value", "")).strip()
            for record in raw_variables
            if record.get("id")
        }
        requested = supplied_by_key.get(key)
        if requested is None:
            requested = _automatic_dimension_default(
                source_id=source_id,
                concept_id=concept_id,
                available=available,
            )
        _validate_dimension_values(
            source_id=source_id,
            concept_id=concept_id,
            values=requested,
            available=available,
        )
        resolved[concept_id] = requested

    strategy: SourceStrategy = "advanced" if extra_concepts else "classic"
    concepts = tuple(concept_lookup.values())
    return SourcePlan(
        source_id=source_id,
        strategy=strategy,
        concepts=concepts,
        country_concept=country_concept,
        series_concept=series_concept,
        time_concept=time_concept,
        extra_dimensions=resolved,
    )


def _automatic_dimension_default(
    *,
    source_id: int,
    concept_id: str,
    available: Mapping[str, str],
) -> tuple[str, ...]:
    key = _concept_key(concept_id)
    if key == "counterpart-area" and "WLD" in available:
        return ("WLD",)
    if len(available) == 1:
        return (next(iter(available)),)

    sample = ", ".join(list(available)[:8]) or "<none>"
    raise ValueError(
        f"World Bank source {source_id} requires an explicit value for dimension "
        f"{concept_id!r}. Available examples: {sample}. Pass --dimensions "
        f"{source_id}:{concept_id}=VALUE"
    )


def _validate_dimension_values(
    *,
    source_id: int,
    concept_id: str,
    values: Sequence[str],
    available: Mapping[str, str],
) -> None:
    if values == ("all",) or values == ("ALL",):
        return
    missing = [value for value in values if value not in available]
    if missing:
        raise ValueError(
            f"World Bank source {source_id} dimension {concept_id!r} does not expose: "
            + ", ".join(missing)
        )


def _fetch_observations_by_source(
    reader: WorldBankReader,
    *,
    country_codes: Sequence[str],
    selections: Sequence[IndicatorSelection],
    plans: Mapping[int, SourcePlan],
    start_year: int,
    end_year: int,
) -> dict[int, list[JsonRecord]]:
    """Fetch each selected series through its source-specific query strategy."""

    records_by_source: dict[int, list[JsonRecord]] = defaultdict(list)
    for selection in selections:
        plan = plans[selection.source_id]
        try:
            if plan.strategy == "classic":
                indicator_records = reader.get_observations(
                    country_codes=country_codes,
                    indicator_codes=[selection.code],
                    start_year=start_year,
                    end_year=end_year,
                    source_id=selection.source_id,
                )
            else:
                dimensions = _advanced_query_dimensions(
                    plan,
                    country_codes=country_codes,
                    indicator_code=selection.code,
                    start_year=start_year,
                    end_year=end_year,
                )
                indicator_records = reader.get_advanced_data(
                    source_id=selection.source_id,
                    dimensions=dimensions,
                )
        except WorldBankAPIError as exc:
            raise WorldBankAPIError(
                f"failed to fetch observations for {selection.qualified_code}: {exc}"
            ) from exc
        records_by_source[selection.source_id].extend(indicator_records)
    return dict(records_by_source)


def _advanced_query_dimensions(
    plan: SourcePlan,
    *,
    country_codes: Sequence[str],
    indicator_code: str,
    start_year: int,
    end_year: int,
) -> dict[str, Sequence[str] | str]:
    values_by_key: dict[str, Sequence[str] | str] = {
        "country": list(country_codes),
        "series": [indicator_code],
        "time": [f"YR{year}" for year in range(start_year, end_year + 1)],
    }
    for concept_id, values in plan.extra_dimensions.items():
        values_by_key[_concept_key(concept_id)] = list(values)

    dimensions: dict[str, Sequence[str] | str] = {}
    for concept_id in plan.concepts:
        key = _concept_key(concept_id)
        selected_values = values_by_key.get(key)
        if selected_values is None:
            raise ValueError(
                f"source {plan.source_id} concept {concept_id!r} has no resolved query values"
            )
        dimensions[concept_id] = selected_values
    return dimensions


def _save_observations_by_source(
    raw_store: RawStore,
    *,
    records_by_source: Mapping[int, list[JsonRecord]],
    selections: Sequence[IndicatorSelection],
    plans: Mapping[int, SourcePlan],
    country_codes: Sequence[str],
    start_year: int,
    end_year: int,
    run_id: str,
    fetched_at: datetime,
) -> tuple[RawArtifact, ...]:
    artifacts: list[RawArtifact] = []
    for source_id in sorted(records_by_source):
        codes = [selection.code for selection in selections if selection.source_id == source_id]
        plan = plans[source_id]
        artifacts.append(
            raw_store.save_records(
                dataset=f"observations_source_{source_id}",
                records=records_by_source[source_id],
                run_id=run_id,
                request_params={
                    "country_codes": list(country_codes),
                    "indicator_codes": codes,
                    "start_year": start_year,
                    "end_year": end_year,
                    "source_id": source_id,
                    "request_strategy": plan.strategy,
                    "extra_dimensions": {
                        concept: list(values) for concept, values in plan.extra_dimensions.items()
                    },
                },
                fetched_at=fetched_at,
            )
        )
    return tuple(artifacts)


def _normalize_observations_by_source(
    records_by_source: Mapping[int, list[JsonRecord]],
    *,
    plans: Mapping[int, SourcePlan],
    run_id: str,
    loaded_at: datetime,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for source_id, records in sorted(records_by_source.items()):
        plan = plans[source_id]
        if plan.strategy == "classic":
            frame = normalize_observations(
                records,
                source_id=source_id,
                run_id=run_id,
                loaded_at=loaded_at,
            )
        else:
            frame = normalize_advanced_observations(
                records,
                source_id=source_id,
                run_id=run_id,
                loaded_at=loaded_at,
            )
        frames.append(frame)
    if not frames:
        return normalize_observations([], source_id=0, run_id=run_id, loaded_at=loaded_at)
    return pd.concat(frames, ignore_index=True)


def _normalize_dimension_filters(
    filters: DimensionFilters | None,
) -> dict[int, dict[str, tuple[str, ...]]]:
    if filters is None:
        return {}
    normalized: dict[int, dict[str, tuple[str, ...]]] = {}
    for source_id, concepts in filters.items():
        if source_id <= 0:
            raise ValueError("dimension filter source ids must be greater than zero")
        normalized_concepts: dict[str, tuple[str, ...]] = {}
        for concept_id, raw_values in concepts.items():
            concept = concept_id.strip()
            if not concept:
                raise ValueError("dimension concept cannot be blank")
            values = tuple(str(value).strip() for value in raw_values if str(value).strip())
            if not values:
                raise ValueError(f"dimension {source_id}:{concept} cannot be empty")
            if len(values) != len(set(values)):
                raise ValueError(f"dimension {source_id}:{concept} values must be unique")
            normalized_concepts[concept] = values
        normalized[source_id] = normalized_concepts
    return normalized


def _resolve_countries(
    config: ApplicationConfig,
    values: Sequence[str] | None,
) -> list[str]:
    selected = list(values) if values is not None else list(config.research.scope.countries)
    normalized = [value.strip().upper() for value in selected if value.strip()]
    if not normalized:
        raise ValueError("at least one country code must be selected")
    if len(normalized) != len(set(normalized)):
        raise ValueError("country codes must be unique")
    invalid = [value for value in normalized if len(value) != 3 or not value.isalpha()]
    if invalid:
        raise ValueError(f"invalid country codes: {', '.join(invalid)}")
    return normalized


def _concept_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _validate_period(start_year: int, end_year: int) -> None:
    if start_year < 1960 or end_year > 2100:
        raise ValueError("year range must stay between 1960 and 2100")
    if end_year < start_year:
        raise ValueError("end_year must be greater than or equal to start_year")


def _write_parquet(frame: pd.DataFrame, path: Path) -> None:
    frame.to_parquet(path, engine="pyarrow", index=False)


def _write_quality_report(
    path: Path,
    *,
    report: QualityReport,
    run_id: str,
    generated_at: datetime,
) -> None:
    payload = {
        "run_id": run_id,
        "generated_at": generated_at.astimezone(UTC).isoformat(),
        **report.to_dict(),
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _generate_run_id() -> str:
    now = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{now}_{uuid4().hex[:8]}"
