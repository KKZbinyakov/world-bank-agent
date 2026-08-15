"""Normalization and semantic enrichment of World Bank indicator metadata."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import pandas as pd

from wb_insight.config import IndicatorRegistry, IndicatorSpec

INDICATOR_COLUMNS = [
    "source_id",
    "source_name",
    "indicator_code",
    "indicator_name",
    "source_unit",
    "alias",
    "name_ru",
    "category",
    "category_source",
    "role",
    "unit",
    "display_unit",
    "unit_source",
    "is_registered",
    "source_note",
    "source_organization",
    "topic_ids",
    "topic_names",
    "run_id",
    "loaded_at",
]


def normalize_indicators(
    records: Sequence[Mapping[str, Any]],
    *,
    run_id: str,
    loaded_at: datetime,
    registry: IndicatorRegistry | None = None,
) -> pd.DataFrame:
    """Convert raw indicator metadata into a stable semantic dataframe schema.

    Registered indicators receive curated aliases, categories and units. Indicators
    absent from the registry are still retained. Their category falls back to the
    first World Bank topic when available and their unit falls back to the source
    unit only when the API explicitly provides one. Missing units are never guessed.
    """

    registry_by_key = _registry_by_key(registry)
    rows: list[dict[str, Any]] = []
    for record in records:
        source = record.get("source")
        source_mapping = source if isinstance(source, Mapping) else {}
        topic_ids, topic_names = _normalize_topics(record.get("topics"))
        indicator_code = _text(record.get("id")).upper()
        source_id = _optional_int(source_mapping.get("id"))
        source_unit = _optional_text(record.get("unit"))
        spec = registry_by_key.get((source_id, indicator_code)) if source_id is not None else None
        semantics = _resolve_semantics(
            spec,
            source_unit=source_unit,
            topic_names=topic_names,
        )
        rows.append(
            {
                "source_id": source_id,
                "source_name": _text(source_mapping.get("value")),
                "indicator_code": indicator_code,
                "indicator_name": _text(record.get("name") or record.get("value")),
                "source_unit": source_unit,
                **semantics,
                "source_note": _text(record.get("sourceNote")),
                "source_organization": _text(record.get("sourceOrganization")),
                "topic_ids": topic_ids,
                "topic_names": topic_names,
                "run_id": run_id,
                "loaded_at": loaded_at,
            }
        )

    frame = pd.DataFrame(rows, columns=INDICATOR_COLUMNS)
    if frame.empty:
        return frame

    frame["source_id"] = pd.to_numeric(frame["source_id"], errors="coerce").astype("Int64")
    frame["is_registered"] = frame["is_registered"].astype("boolean")
    for column in (
        "source_unit",
        "alias",
        "name_ru",
        "category",
        "category_source",
        "role",
        "unit",
        "display_unit",
        "unit_source",
    ):
        frame[column] = frame[column].astype("string")
    frame["loaded_at"] = pd.to_datetime(frame["loaded_at"], utc=True)
    return frame.sort_values(["source_id", "indicator_code"], kind="stable").reset_index(drop=True)


def _registry_by_key(
    registry: IndicatorRegistry | None,
) -> dict[tuple[int, str], IndicatorSpec]:
    if registry is None:
        return {}
    return {
        (registry.effective_source_id(indicator), indicator.code.upper()): indicator
        for indicator in registry.indicators
    }


def _resolve_semantics(
    spec: IndicatorSpec | None,
    *,
    source_unit: str | None,
    topic_names: str,
) -> dict[str, object]:
    if spec is not None:
        unit = spec.unit or source_unit
        display_unit = spec.display_unit or source_unit or spec.unit
        return {
            "alias": spec.alias,
            "name_ru": spec.name_ru,
            "category": spec.category,
            "category_source": "registry",
            "role": spec.role,
            "unit": unit,
            "display_unit": display_unit,
            "unit_source": (
                "registry" if spec.unit else ("world_bank" if source_unit else "missing")
            ),
            "is_registered": True,
        }

    category = _category_from_topics(topic_names)
    return {
        "alias": None,
        "name_ru": None,
        "category": category,
        "category_source": "world_bank_topic" if category != "uncategorized" else "missing",
        "role": "unassigned",
        "unit": source_unit,
        "display_unit": source_unit,
        "unit_source": "world_bank" if source_unit else "missing",
        "is_registered": False,
    }


def _category_from_topics(topic_names: str) -> str:
    first_topic = next((part.strip() for part in topic_names.split("|") if part.strip()), "")
    if not first_topic:
        return "uncategorized"
    normalized = re.sub(r"[^a-z0-9]+", "_", first_topic.lower()).strip("_")
    return normalized or "uncategorized"


def _normalize_topics(value: object) -> tuple[str, str]:
    if not isinstance(value, list):
        return "", ""

    topic_ids: list[str] = []
    topic_names: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        topic_id = _text(item.get("id"))
        topic_name = _text(item.get("value"))
        if topic_id:
            topic_ids.append(topic_id)
        if topic_name:
            topic_names.append(topic_name)
    return "|".join(topic_ids), "|".join(topic_names)


def _optional_int(value: object) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _optional_text(value: object) -> str | None:
    text = _text(value)
    return text or None


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()
