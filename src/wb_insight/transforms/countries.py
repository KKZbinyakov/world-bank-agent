"""Normalization of World Bank country metadata."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import pandas as pd

COUNTRY_COLUMNS = [
    "country_code",
    "iso2_code",
    "country_name",
    "region_code",
    "region_name",
    "admin_region_code",
    "admin_region_name",
    "income_level_code",
    "income_level_name",
    "lending_type_code",
    "lending_type_name",
    "capital_city",
    "longitude",
    "latitude",
    "is_aggregate",
    "run_id",
    "loaded_at",
]


def normalize_countries(
    records: Sequence[Mapping[str, Any]],
    *,
    run_id: str,
    loaded_at: datetime,
) -> pd.DataFrame:
    """Convert raw country records into a stable dataframe schema."""

    rows: list[dict[str, Any]] = []
    for record in records:
        region_code = _nested_text(record, "region", "id")
        region_name = _nested_text(record, "region", "value")
        rows.append(
            {
                "country_code": _text(record.get("id")).upper(),
                "iso2_code": _text(record.get("iso2Code")).upper(),
                "country_name": _text(record.get("name")),
                "region_code": region_code,
                "region_name": region_name,
                "admin_region_code": _nested_text(record, "adminregion", "id"),
                "admin_region_name": _nested_text(record, "adminregion", "value"),
                "income_level_code": _nested_text(record, "incomeLevel", "id"),
                "income_level_name": _nested_text(record, "incomeLevel", "value"),
                "lending_type_code": _nested_text(record, "lendingType", "id"),
                "lending_type_name": _nested_text(record, "lendingType", "value"),
                "capital_city": _text(record.get("capitalCity")),
                "longitude": record.get("longitude"),
                "latitude": record.get("latitude"),
                "is_aggregate": _is_aggregate(region_code, region_name),
                "run_id": run_id,
                "loaded_at": loaded_at,
            }
        )

    frame = pd.DataFrame(rows, columns=COUNTRY_COLUMNS)
    if frame.empty:
        return frame

    frame["longitude"] = pd.to_numeric(frame["longitude"], errors="coerce")
    frame["latitude"] = pd.to_numeric(frame["latitude"], errors="coerce")
    frame["is_aggregate"] = frame["is_aggregate"].astype("boolean")
    frame["loaded_at"] = pd.to_datetime(frame["loaded_at"], utc=True)
    return frame.sort_values("country_code", kind="stable").reset_index(drop=True)


def _is_aggregate(region_code: str, region_name: str) -> bool:
    return region_code.upper() == "NA" or region_name.strip().lower() in {
        "aggregate",
        "aggregates",
    }


def _nested_text(record: Mapping[str, Any], key: str, nested_key: str) -> str:
    nested = record.get(key)
    if not isinstance(nested, Mapping):
        return ""
    return _text(nested.get(nested_key))


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()
