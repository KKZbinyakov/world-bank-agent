"""Normalization and semantic enrichment of World Bank observations."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import pandas as pd

BASE_OBSERVATION_COLUMNS = [
    "source_id",
    "indicator_code",
    "indicator_name",
    "country_code",
    "country_name",
    "year",
    "value",
    "dimensions_json",
    "dimension_count",
    "source_unit",
    "observation_status",
    "decimal_scale",
    "is_missing",
    "run_id",
    "loaded_at",
]

OBSERVATION_COLUMNS = [
    "source_id",
    "indicator_code",
    "indicator_name",
    "indicator_alias",
    "indicator_name_ru",
    "indicator_category",
    "category_source",
    "indicator_role",
    "country_code",
    "country_name",
    "year",
    "value",
    "dimensions_json",
    "dimension_count",
    "source_unit",
    "unit",
    "display_unit",
    "unit_source",
    "is_registered",
    "observation_status",
    "decimal_scale",
    "is_missing",
    "run_id",
    "loaded_at",
]


def normalize_observations(
    records: Sequence[Mapping[str, Any]],
    *,
    source_id: int,
    run_id: str,
    loaded_at: datetime,
) -> pd.DataFrame:
    """Convert classic indicator records into long-format analytical rows."""

    rows: list[dict[str, Any]] = []
    for record in records:
        indicator = record.get("indicator")
        indicator_mapping = indicator if isinstance(indicator, Mapping) else {}
        country = record.get("country")
        country_mapping = country if isinstance(country, Mapping) else {}
        rows.append(
            {
                "source_id": source_id,
                "indicator_code": _text(indicator_mapping.get("id")).upper(),
                "indicator_name": _text(indicator_mapping.get("value")),
                "country_code": _text(record.get("countryiso3code")).upper(),
                "country_name": _text(country_mapping.get("value")),
                "year": record.get("date"),
                "value": record.get("value"),
                "dimensions_json": "{}",
                "dimension_count": 0,
                "source_unit": _optional_text(record.get("unit")),
                "observation_status": _text(record.get("obs_status")),
                "decimal_scale": record.get("decimal"),
                "run_id": run_id,
                "loaded_at": loaded_at,
            }
        )
    return _finalize_observation_frame(rows, source_id=source_id)


def normalize_advanced_observations(
    records: Sequence[Mapping[str, Any]],
    *,
    source_id: int,
    run_id: str,
    loaded_at: datetime,
) -> pd.DataFrame:
    """Normalize records returned by the multidimensional Advanced Data API.

    Country, Series and Time become first-class analytical columns. Every additional
    source dimension is preserved losslessly in ``dimensions_json`` so rows from
    multidimensional sources never collapse into the same observation key.
    """

    rows: list[dict[str, Any]] = []
    for record in records:
        raw_variables = record.get("variable")
        variables = raw_variables if isinstance(raw_variables, list) else []

        country_code = ""
        country_name = ""
        indicator_code = ""
        indicator_name = ""
        year: int | str | None = None
        extra_dimensions: dict[str, dict[str, str]] = {}

        for raw_variable in variables:
            if not isinstance(raw_variable, Mapping):
                continue
            concept = _text(raw_variable.get("concept"))
            concept_key = _concept_key(concept)
            variable_id = _text(raw_variable.get("id"))
            variable_value = _text(raw_variable.get("value"))

            if concept_key == "country":
                country_code = variable_id.upper()
                country_name = variable_value
            elif concept_key == "series":
                indicator_code = variable_id.upper()
                indicator_name = variable_value
            elif concept_key == "time":
                year = _year_value(variable_id, variable_value)
            else:
                extra_dimensions[concept or concept_key] = {
                    "id": variable_id,
                    "value": variable_value,
                }

        dimensions_json = json.dumps(
            extra_dimensions,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        rows.append(
            {
                "source_id": source_id,
                "indicator_code": indicator_code,
                "indicator_name": indicator_name,
                "country_code": country_code,
                "country_name": country_name,
                "year": year,
                "value": record.get("value"),
                "dimensions_json": dimensions_json,
                "dimension_count": len(extra_dimensions),
                "source_unit": None,
                "observation_status": "",
                "decimal_scale": None,
                "run_id": run_id,
                "loaded_at": loaded_at,
            }
        )
    return _finalize_observation_frame(rows, source_id=source_id)


def enrich_observations_with_indicator_semantics(
    observations: pd.DataFrame,
    indicators: pd.DataFrame,
) -> pd.DataFrame:
    """Attach curated or discovered indicator semantics to observation rows."""

    if observations.empty:
        return pd.DataFrame(columns=OBSERVATION_COLUMNS)

    semantic_columns = [
        "source_id",
        "indicator_code",
        "alias",
        "name_ru",
        "category",
        "category_source",
        "role",
        "unit",
        "display_unit",
        "unit_source",
        "is_registered",
    ]
    missing = [column for column in semantic_columns if column not in indicators.columns]
    if missing:
        raise ValueError(f"indicator metadata is missing semantic columns: {', '.join(missing)}")

    semantics = indicators[semantic_columns].rename(
        columns={
            "alias": "indicator_alias",
            "name_ru": "indicator_name_ru",
            "category": "indicator_category",
            "role": "indicator_role",
        }
    )
    frame = observations.merge(
        semantics,
        how="left",
        on=["source_id", "indicator_code"],
        validate="many_to_one",
    )
    frame = frame.reindex(columns=OBSERVATION_COLUMNS)
    for column in (
        "indicator_alias",
        "indicator_name_ru",
        "indicator_category",
        "category_source",
        "indicator_role",
        "unit",
        "display_unit",
        "unit_source",
    ):
        frame[column] = frame[column].astype("string")
    frame["is_registered"] = frame["is_registered"].astype("boolean")
    return frame.sort_values(
        ["source_id", "indicator_code", "country_code", "year", "dimensions_json"],
        kind="stable",
    ).reset_index(drop=True)


def _finalize_observation_frame(
    rows: list[dict[str, Any]],
    *,
    source_id: int,
) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=BASE_OBSERVATION_COLUMNS)

    frame["source_id"] = pd.Series(source_id, index=frame.index, dtype="Int64")
    frame["year"] = pd.to_numeric(frame["year"], errors="coerce").astype("Int64")
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce").astype("Float64")
    frame["dimensions_json"] = frame["dimensions_json"].astype("string")
    frame["dimension_count"] = pd.to_numeric(frame["dimension_count"], errors="coerce").astype(
        "Int64"
    )
    frame["source_unit"] = frame["source_unit"].astype("string")
    frame["decimal_scale"] = pd.to_numeric(frame["decimal_scale"], errors="coerce").astype("Int64")
    frame["is_missing"] = frame["value"].isna().astype("boolean")
    frame["loaded_at"] = pd.to_datetime(frame["loaded_at"], utc=True)
    frame = frame.reindex(columns=BASE_OBSERVATION_COLUMNS)
    return frame.sort_values(
        ["source_id", "indicator_code", "country_code", "year", "dimensions_json"],
        kind="stable",
    ).reset_index(drop=True)


def _year_value(variable_id: str, variable_value: str) -> int | str | None:
    for candidate in (variable_value, variable_id):
        text = candidate.strip().upper()
        if text.startswith("YR") and text[2:].isdigit():
            return int(text[2:])
        if text.isdigit() and len(text) == 4:
            return int(text)
    return variable_value or variable_id or None


def _concept_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _optional_text(value: object) -> str | None:
    text = _text(value)
    return text or None


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()
