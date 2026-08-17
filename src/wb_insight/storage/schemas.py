"""ClickHouse schemas and DataFrame-to-ClickHouse type helpers."""

from __future__ import annotations

import re
from collections.abc import Sequence

import pandas as pd
from pandas.api import types as ptypes

COUNTRY_TABLE_COLUMNS = [
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

INDICATOR_TABLE_COLUMNS = [
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

OBSERVATION_TABLE_COLUMNS = [
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

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def ensure_safe_identifier(value: str) -> str:
    """Validate an identifier before interpolating it into ClickHouse SQL."""

    if not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"unsafe ClickHouse identifier: {value!r}")
    return value


def ensure_columns(frame: pd.DataFrame, required: Sequence[str], dataset: str) -> None:
    """Raise a clear error if an input DataFrame does not match the expected schema."""

    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"{dataset} is missing required columns: {', '.join(missing)}")


def clickhouse_type_for_series(series: pd.Series) -> str:
    """Infer a conservative ClickHouse type for a DataFrame column.

    Dynamic Gold marts intentionally use broad nullable numeric types so a later run
    can contain nulls without forcing a schema migration. Stable Silver tables use
    explicit SQL DDL instead.
    """

    dtype = series.dtype
    if ptypes.is_bool_dtype(dtype):
        return "Nullable(UInt8)" if series.isna().any() else "UInt8"
    if ptypes.is_integer_dtype(dtype):
        return "Nullable(Int64)" if series.isna().any() else "Int64"
    if ptypes.is_float_dtype(dtype):
        return "Nullable(Float64)"
    if ptypes.is_datetime64_any_dtype(dtype):
        return "Nullable(DateTime64(3, 'UTC'))" if series.isna().any() else "DateTime64(3, 'UTC')"
    return "Nullable(String)" if series.isna().any() else "String"


def dynamic_table_ddl(
    table_name: str,
    frame: pd.DataFrame,
    *,
    order_by: Sequence[str] = (),
) -> str:
    """Build DDL for a replaceable DataLens-oriented dynamic table."""

    safe_table = ensure_safe_identifier(table_name)
    if frame.columns.empty:
        raise ValueError("cannot create a dynamic ClickHouse table without columns")

    column_defs = []
    for column in frame.columns:
        safe_column = ensure_safe_identifier(str(column))
        column_defs.append(f"    `{safe_column}` {clickhouse_type_for_series(frame[column])}")

    safe_order = [ensure_safe_identifier(column) for column in order_by if column in frame.columns]
    order_expression = ", ".join(f"`{column}`" for column in safe_order) or "tuple()"
    return (
        f"CREATE TABLE `{safe_table}` (\n"
        + ",\n".join(column_defs)
        + "\n) ENGINE = MergeTree\n"
        + f"ORDER BY ({order_expression})"
    )
