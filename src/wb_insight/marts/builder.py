"""Universal DataLens-oriented mart builder for processed World Bank runs."""

from __future__ import annotations

import ast
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import yaml

DimensionMode = Literal["columns", "error"]

COUNTRY_DIMENSION_CANDIDATES = (
    "country_code",
    "country_name",
    "region_id",
    "region_name",
    "income_level_id",
    "income_level_name",
    "lending_type_id",
    "lending_type_name",
    "capital_city",
    "longitude",
    "latitude",
)

INDICATOR_METADATA_CANDIDATES = (
    "source_id",
    "source_name",
    "indicator_code",
    "indicator_name",
    "source_unit",
    "source_note",
    "source_organization",
    "topic_ids",
    "topic_names",
    "alias",
    "name_ru",
    "category",
    "category_source",
    "role",
    "unit",
    "display_unit",
    "unit_source",
    "is_registered",
)


@dataclass(frozen=True, slots=True)
class DerivedMetricSpec:
    """A safe arithmetic expression evaluated over already-built wide columns."""

    name: str
    expression: str
    round_digits: int | None = None


@dataclass(frozen=True, slots=True)
class MartConfig:
    """Optional overrides for presentation aliases and derived metrics."""

    column_aliases: dict[str, str]
    country_labels: dict[str, str]
    country_label_column: str
    derived_metrics: tuple[DerivedMetricSpec, ...]


@dataclass(frozen=True, slots=True)
class MartBuildResult:
    """In-memory result of a universal mart build."""

    long: pd.DataFrame
    wide: pd.DataFrame
    metric_catalog: pd.DataFrame
    manifest: dict[str, Any]


class MartBuildError(ValueError):
    """Raised when processed data cannot be transformed without losing meaning."""


def load_mart_config(path: Path | None) -> MartConfig:
    """Load optional YAML config; return an empty configuration when omitted."""

    if path is None:
        return MartConfig({}, {}, "country_label", ())

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:  # pragma: no cover - surfaced to CLI
        raise MartBuildError(f"cannot read mart config {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise MartBuildError("mart config must contain a YAML mapping")

    aliases_raw = raw.get("column_aliases", {}) or {}
    labels_raw = raw.get("country_labels", {}) or {}
    derived_raw = raw.get("derived_metrics", []) or []
    label_column = str(raw.get("country_label_column", "country_label")).strip()

    if not isinstance(aliases_raw, dict):
        raise MartBuildError("column_aliases must be a mapping")
    if not isinstance(labels_raw, dict):
        raise MartBuildError("country_labels must be a mapping")
    if not isinstance(derived_raw, list):
        raise MartBuildError("derived_metrics must be a list")
    if not _is_safe_column_name(label_column):
        raise MartBuildError(f"invalid country_label_column: {label_column}")

    aliases: dict[str, str] = {}
    for selector, alias in aliases_raw.items():
        selector_text = str(selector).strip()
        alias_text = str(alias).strip()
        if not selector_text:
            raise MartBuildError("column_aliases keys cannot be blank")
        if not _is_safe_column_name(alias_text):
            raise MartBuildError(f"invalid column alias {alias_text!r} for {selector_text!r}")
        aliases[selector_text] = alias_text

    labels = {str(code).strip().upper(): str(label) for code, label in labels_raw.items()}

    derived: list[DerivedMetricSpec] = []
    for item in derived_raw:
        if not isinstance(item, dict):
            raise MartBuildError("each derived_metrics item must be a mapping")
        name = str(item.get("name", "")).strip()
        expression = str(item.get("expression", "")).strip()
        if not _is_safe_column_name(name):
            raise MartBuildError(f"invalid derived metric name: {name!r}")
        if not expression:
            raise MartBuildError(f"derived metric {name!r} has an empty expression")
        _validate_expression(expression)
        round_value = item.get("round")
        round_digits = int(round_value) if round_value is not None else None
        derived.append(DerivedMetricSpec(name, expression, round_digits))

    return MartConfig(
        column_aliases=aliases,
        country_labels=labels,
        country_label_column=label_column,
        derived_metrics=tuple(derived),
    )


def build_marts(
    countries: pd.DataFrame,
    indicators: pd.DataFrame,
    observations: pd.DataFrame,
    *,
    config: MartConfig | None = None,
    country_codes: set[str] | None = None,
    indicator_selectors: set[str] | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
    dimension_mode: DimensionMode = "columns",
    complete_grid: bool = True,
) -> MartBuildResult:
    """Build generic long/wide marts from one processed ingestion run.

    The function is intentionally indicator-agnostic. Registered aliases are used
    when available; otherwise deterministic source-qualified fallback names are
    generated. Extra source dimensions are preserved in the long mart and, by
    default, encoded into distinct wide column names rather than aggregated.
    """

    cfg = config or MartConfig({}, {}, "country_label", ())
    _validate_input_frames(countries, indicators, observations)

    filtered = observations.copy()
    filtered["source_id"] = pd.to_numeric(filtered["source_id"], errors="raise").astype("Int64")
    filtered["year"] = pd.to_numeric(filtered["year"], errors="raise").astype("Int64")
    filtered["country_code"] = filtered["country_code"].astype("string").str.upper()
    filtered["indicator_code"] = filtered["indicator_code"].astype("string")
    filtered["metric_key"] = _metric_key_series(filtered)

    if country_codes is not None:
        wanted_countries = {code.strip().upper() for code in country_codes if code.strip()}
        unknown = wanted_countries - set(filtered["country_code"].dropna().astype(str))
        if unknown:
            missing_countries = ", ".join(sorted(unknown))
            raise MartBuildError(f"countries are absent from this run: {missing_countries}")
        filtered = filtered[filtered["country_code"].isin(wanted_countries)]

    if start_year is not None:
        filtered = filtered[filtered["year"] >= start_year]
    if end_year is not None:
        filtered = filtered[filtered["year"] <= end_year]
    if start_year is not None and end_year is not None and start_year > end_year:
        raise MartBuildError("start_year cannot be greater than end_year")

    indicator_meta = _prepare_indicator_metadata(indicators)
    filtered = _fill_indicator_semantics(filtered, indicator_meta)

    if indicator_selectors is not None:
        resolved_keys = _resolve_indicator_selectors(indicator_selectors, filtered)
        filtered = filtered[filtered["metric_key"].isin(resolved_keys)]

    if filtered.empty:
        raise MartBuildError("no observations remain after mart filters")

    filtered["dimensions_json"] = filtered.get(
        "dimensions_json", pd.Series("{}", index=filtered.index, dtype="string")
    ).fillna("{}")
    filtered["dimension_signature"] = filtered["dimensions_json"].map(_canonical_dimensions_json)

    if dimension_mode == "error":
        nonempty = filtered[filtered["dimension_signature"] != "{}"]
        if not nonempty.empty:
            sample = nonempty[["metric_key", "dimension_signature"]].drop_duplicates().head(10)
            raise MartBuildError(
                "multidimensional observations are present; use dimension_mode='columns' "
                "or filter dimensions during ingestion. Examples:\n" + sample.to_string(index=False)
            )
    elif dimension_mode != "columns":
        raise MartBuildError(f"unsupported dimension mode: {dimension_mode}")

    metric_catalog = _build_metric_catalog(filtered, cfg)
    name_lookup = {
        (str(row.metric_key), str(row.dimension_signature)): str(row.wide_column)
        for row in metric_catalog.itertuples(index=False)
    }
    filtered["wide_column"] = [
        name_lookup[(str(metric_key), str(signature))]
        for metric_key, signature in zip(
            filtered["metric_key"], filtered["dimension_signature"], strict=True
        )
    ]

    duplicate_mask = filtered.duplicated(subset=["country_code", "year", "wide_column"], keep=False)
    if bool(duplicate_mask.any()):
        sample = filtered.loc[
            duplicate_mask,
            ["country_code", "year", "metric_key", "dimensions_json", "wide_column"],
        ].head(10)
        raise MartBuildError(
            "multiple values map to the same country/year/wide column; refusing to aggregate:\n"
            + sample.to_string(index=False)
        )

    country_meta = _prepare_country_metadata(countries)
    long_frame = filtered.merge(country_meta, on="country_code", how="left", validate="many_to_one")
    if cfg.country_labels:
        long_frame[cfg.country_label_column] = long_frame["country_code"].map(cfg.country_labels)

    wide = filtered.pivot(
        index=["country_code", "year"],
        columns="wide_column",
        values="value",
    )

    if complete_grid:
        run_countries = sorted(filtered["country_code"].dropna().astype(str).unique())
        minimum_year = int(filtered["year"].min())
        maximum_year = int(filtered["year"].max())
        full_index = pd.MultiIndex.from_product(
            [run_countries, range(minimum_year, maximum_year + 1)],
            names=["country_code", "year"],
        )
        wide = wide.reindex(full_index)

    wide = wide.reset_index()
    wide.columns.name = None
    wide = wide.merge(country_meta, on="country_code", how="left", validate="many_to_one")
    if cfg.country_labels:
        wide[cfg.country_label_column] = wide["country_code"].map(cfg.country_labels)

    wide = _apply_derived_metrics(wide, cfg.derived_metrics)
    wide = _order_wide_columns(wide, metric_catalog, cfg)
    long_frame = _order_long_columns(long_frame)

    manifest = _build_manifest(long_frame, wide, metric_catalog, cfg, complete_grid)
    return MartBuildResult(
        long=long_frame,
        wide=wide,
        metric_catalog=metric_catalog,
        manifest=manifest,
    )


def export_run_to_csv(
    run_dir: Path,
    output_dir: Path,
    *,
    config_path: Path | None = None,
    country_codes: set[str] | None = None,
    indicator_selectors: set[str] | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
    dimension_mode: DimensionMode = "columns",
    complete_grid: bool = True,
) -> dict[str, Path]:
    """Read a processed run, build generic marts, and export DataLens-ready CSVs."""

    countries = pd.read_parquet(run_dir / "countries.parquet")
    indicators = pd.read_parquet(run_dir / "indicators.parquet")
    observations = pd.read_parquet(run_dir / "observations.parquet")
    config = load_mart_config(config_path)

    result = build_marts(
        countries,
        indicators,
        observations,
        config=config,
        country_codes=country_codes,
        indicator_selectors=indicator_selectors,
        start_year=start_year,
        end_year=end_year,
        dimension_mode=dimension_mode,
        complete_grid=complete_grid,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "wide": output_dir / "worldbank_datalens_wide.csv",
        "long": output_dir / "worldbank_datalens_long.csv",
        "metric_catalog": output_dir / "worldbank_metric_catalog.csv",
        "manifest": output_dir / "mart_manifest.json",
    }
    result.wide.to_csv(paths["wide"], index=False, encoding="utf-8-sig", na_rep="")
    result.long.to_csv(paths["long"], index=False, encoding="utf-8-sig", na_rep="")
    result.metric_catalog.to_csv(
        paths["metric_catalog"], index=False, encoding="utf-8-sig", na_rep=""
    )
    paths["manifest"].write_text(
        json.dumps(result.manifest, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return paths


def _validate_input_frames(
    countries: pd.DataFrame,
    indicators: pd.DataFrame,
    observations: pd.DataFrame,
) -> None:
    requirements = {
        "countries": (countries, {"country_code"}),
        "indicators": (indicators, {"source_id", "indicator_code"}),
        "observations": (
            observations,
            {"source_id", "indicator_code", "country_code", "year", "value"},
        ),
    }
    for name, (frame, required) in requirements.items():
        missing = required - set(frame.columns)
        if missing:
            raise MartBuildError(f"{name} is missing columns: {', '.join(sorted(missing))}")


def _prepare_country_metadata(countries: pd.DataFrame) -> pd.DataFrame:
    available = [name for name in COUNTRY_DIMENSION_CANDIDATES if name in countries.columns]
    if "country_code" not in available:
        available.insert(0, "country_code")
    frame = countries[available].copy()
    frame["country_code"] = frame["country_code"].astype("string").str.upper()
    if bool(frame["country_code"].duplicated().any()):
        raise MartBuildError("countries.parquet contains duplicate country_code values")
    return frame


def _prepare_indicator_metadata(indicators: pd.DataFrame) -> pd.DataFrame:
    available = [name for name in INDICATOR_METADATA_CANDIDATES if name in indicators.columns]
    frame = indicators[available].copy()
    frame["source_id"] = pd.to_numeric(frame["source_id"], errors="raise").astype("Int64")
    frame["indicator_code"] = frame["indicator_code"].astype("string")
    if bool(frame.duplicated(subset=["source_id", "indicator_code"]).any()):
        raise MartBuildError(
            "indicators.parquet contains duplicate (source_id, indicator_code) keys"
        )
    return frame


def _fill_indicator_semantics(
    observations: pd.DataFrame,
    indicator_meta: pd.DataFrame,
) -> pd.DataFrame:
    meta_columns = [
        column for column in indicator_meta.columns if column not in {"source_id", "indicator_code"}
    ]
    merged = observations.merge(
        indicator_meta,
        on=["source_id", "indicator_code"],
        how="left",
        suffixes=("", "__catalog"),
        validate="many_to_one",
    )
    for column in meta_columns:
        catalog_column = f"{column}__catalog"
        if catalog_column not in merged.columns:
            continue
        if column not in observations.columns:
            merged[column] = merged[catalog_column]
        else:
            original = merged[column]
            missing = original.isna()
            if pd.api.types.is_string_dtype(original.dtype) or original.dtype == object:
                missing = missing | original.astype("string").str.strip().eq("")
            merged.loc[missing, column] = merged.loc[missing, catalog_column]
        merged = merged.drop(columns=[catalog_column])
    return merged


def _resolve_indicator_selectors(selectors: set[str], frame: pd.DataFrame) -> set[str]:
    available_keys = set(frame["metric_key"].dropna().astype(str))
    alias_map: dict[str, set[str]] = {}
    code_map: dict[str, set[str]] = {}

    if "alias" in frame.columns:
        alias_rows = frame.loc[frame["alias"].notna(), ["metric_key", "alias"]].drop_duplicates()
        for row in alias_rows.itertuples(index=False):
            alias = str(row.alias).strip()
            if alias:
                alias_map.setdefault(alias, set()).add(str(row.metric_key))

    code_rows = frame[["metric_key", "indicator_code"]].drop_duplicates()
    for row in code_rows.itertuples(index=False):
        code_map.setdefault(str(row.indicator_code), set()).add(str(row.metric_key))

    resolved: set[str] = set()
    for raw_selector in selectors:
        selector = raw_selector.strip()
        if not selector:
            continue
        if selector in available_keys:
            resolved.add(selector)
            continue
        alias_matches = alias_map.get(selector, set())
        if len(alias_matches) == 1:
            resolved.update(alias_matches)
            continue
        if len(alias_matches) > 1:
            raise MartBuildError(f"indicator alias is ambiguous in this run: {selector}")
        code_matches = code_map.get(selector, set())
        if len(code_matches) == 1:
            resolved.update(code_matches)
            continue
        if len(code_matches) > 1:
            raise MartBuildError(
                f"indicator code {selector} occurs in multiple sources; use SOURCE_ID:CODE"
            )
        raise MartBuildError(f"indicator selector is absent from this run: {selector}")

    if not resolved:
        raise MartBuildError("indicator selector list resolved to no metrics")
    return resolved


def _build_metric_catalog(frame: pd.DataFrame, config: MartConfig) -> pd.DataFrame:
    metadata_columns = [
        column
        for column in (
            "metric_key",
            "source_id",
            "source_name",
            "indicator_code",
            "indicator_name",
            "alias",
            "name_ru",
            "category",
            "role",
            "unit",
            "display_unit",
            "unit_source",
            "is_registered",
            "dimension_signature",
        )
        if column in frame.columns
    ]
    catalog = frame[metadata_columns].drop_duplicates(subset=["metric_key", "dimension_signature"])
    catalog = catalog.sort_values(["source_id", "indicator_code", "dimension_signature"])

    base_names: list[str] = []
    for row in catalog.itertuples(index=False):
        metric_key = str(row.metric_key)
        override = config.column_aliases.get(metric_key)
        alias_value = getattr(row, "alias", None)
        alias = (
            str(alias_value).strip() if alias_value is not None and pd.notna(alias_value) else ""
        )
        if override:
            base = override
        elif alias and _is_safe_column_name(alias):
            base = alias
        else:
            source_id = int(str(row.source_id))
            code = str(row.indicator_code)
            base = f"s{source_id}_{_slugify(code)}"
        base_names.append(base)

    catalog = catalog.copy()
    catalog["base_column"] = base_names
    wide_names: list[str] = []
    used: set[str] = set()
    for row in catalog.itertuples(index=False):
        base = str(row.base_column)
        signature = str(row.dimension_signature)
        candidate = base if signature == "{}" else f"{base}__{_dimension_suffix(signature)}"
        if candidate in used:
            digest = hashlib.sha1(
                f"{row.metric_key}|{signature}".encode(), usedforsecurity=False
            ).hexdigest()[:8]
            candidate = f"{candidate}__{digest}"
        used.add(candidate)
        wide_names.append(candidate)
    catalog["wide_column"] = wide_names
    return catalog.reset_index(drop=True)


def _apply_derived_metrics(
    frame: pd.DataFrame,
    derived_metrics: tuple[DerivedMetricSpec, ...],
) -> pd.DataFrame:
    result = frame.copy()
    for metric in derived_metrics:
        if metric.name in result.columns:
            raise MartBuildError(f"derived metric would overwrite existing column: {metric.name}")
        value = _evaluate_expression(metric.expression, result)
        if metric.round_digits is not None:
            value = value.round(metric.round_digits)
        result[metric.name] = value
    return result


def _order_wide_columns(
    frame: pd.DataFrame,
    metric_catalog: pd.DataFrame,
    config: MartConfig,
) -> pd.DataFrame:
    dimensions = [column for column in COUNTRY_DIMENSION_CANDIDATES if column in frame.columns]
    if config.country_labels and config.country_label_column in frame.columns:
        dimensions.insert(1 if dimensions else 0, config.country_label_column)
    if "year" in frame.columns and "year" not in dimensions:
        dimensions.append("year")

    metric_columns = [
        str(value)
        for value in metric_catalog["wide_column"].tolist()
        if str(value) in frame.columns
    ]
    derived = [metric.name for metric in config.derived_metrics if metric.name in frame.columns]
    ordered = list(dict.fromkeys([*dimensions, *metric_columns, *derived]))
    extras = [column for column in frame.columns if column not in ordered]
    return frame[[*ordered, *extras]]


def _order_long_columns(frame: pd.DataFrame) -> pd.DataFrame:
    preferred = [
        "country_code",
        "country_name",
        "region_name",
        "income_level_name",
        "latitude",
        "longitude",
        "year",
        "source_id",
        "source_name",
        "indicator_code",
        "indicator_name",
        "metric_key",
        "alias",
        "name_ru",
        "category",
        "role",
        "value",
        "unit",
        "display_unit",
        "dimensions_json",
        "dimension_signature",
        "wide_column",
        "is_missing",
        "run_id",
        "loaded_at",
    ]
    ordered = [column for column in preferred if column in frame.columns]
    extras = [column for column in frame.columns if column not in ordered]
    return frame[[*ordered, *extras]]


def _build_manifest(
    long_frame: pd.DataFrame,
    wide: pd.DataFrame,
    metric_catalog: pd.DataFrame,
    config: MartConfig,
    complete_grid: bool,
) -> dict[str, Any]:
    values = long_frame["value"]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "rows": {
            "long": len(long_frame),
            "wide": len(wide),
            "metric_catalog": len(metric_catalog),
        },
        "scope": {
            "countries": int(long_frame["country_code"].nunique()),
            "years": [int(long_frame["year"].min()), int(long_frame["year"].max())],
            "source_indicator_pairs": int(long_frame["metric_key"].nunique()),
            "wide_metric_columns": int(metric_catalog["wide_column"].nunique()),
            "complete_grid": complete_grid,
        },
        "quality": {
            "missing_values": int(values.isna().sum()),
            "value_rows": int(values.notna().sum()),
            "missing_ratio": float(values.isna().mean()),
        },
        "presentation": {
            "configured_column_aliases": len(config.column_aliases),
            "configured_country_labels": len(config.country_labels),
            "derived_metrics": [metric.name for metric in config.derived_metrics],
        },
    }


def _metric_key_series(frame: pd.DataFrame) -> pd.Series:
    return (
        frame["source_id"].astype("Int64").astype("string")
        + ":"
        + frame["indicator_code"].astype("string")
    )


def _canonical_dimensions_json(value: object) -> str:
    if value is None or value is pd.NA:
        return "{}"
    if isinstance(value, float) and math.isnan(value):
        return "{}"
    if isinstance(value, dict):
        payload: object = value
    else:
        text = str(value).strip()
        if not text or text == "{}":
            return "{}"
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MartBuildError(f"invalid dimensions_json value: {text!r}") from exc
    if payload in ({}, None):
        return "{}"
    if not isinstance(payload, dict):
        raise MartBuildError("dimensions_json must encode a JSON object")
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _dimension_suffix(signature: str) -> str:
    payload = json.loads(signature)
    if not isinstance(payload, dict):  # pragma: no cover - canonicalizer prevents this
        raise MartBuildError("dimension signature must be a JSON object")
    parts: list[str] = []
    for concept, raw_value in sorted(payload.items()):
        concept_slug = _slugify(str(concept))
        if isinstance(raw_value, dict):
            value = raw_value.get("id") or raw_value.get("value") or "unknown"
        else:
            value = raw_value
        parts.append(f"{concept_slug}_{_slugify(str(value))}")
    return "__".join(parts) or "dimensions"


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return normalized or "metric"


def _is_safe_column_name(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z][a-z0-9_]*", value))


def _validate_expression(expression: str) -> None:
    tree = ast.parse(expression, mode="eval")
    allowed = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Name,
        ast.Load,
        ast.Constant,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Pow,
        ast.Mod,
        ast.UAdd,
        ast.USub,
    )
    for node in ast.walk(tree):
        if not isinstance(node, allowed):
            raise MartBuildError(
                f"derived expression contains unsupported syntax: {type(node).__name__}"
            )
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise MartBuildError("derived expressions may contain only numeric constants")


def _evaluate_expression(expression: str, frame: pd.DataFrame) -> pd.Series:
    _validate_expression(expression)
    tree = ast.parse(expression, mode="eval")

    def evaluate(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Name):
            if node.id not in frame.columns:
                raise MartBuildError(f"derived expression references missing column: {node.id}")
            return frame[node.id]
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.UnaryOp):
            value = evaluate(node.operand)
            if isinstance(node.op, ast.UAdd):
                return value
            if isinstance(node.op, ast.USub):
                return -value
        if isinstance(node, ast.BinOp):
            left = evaluate(node.left)
            right = evaluate(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.Pow):
                return left**right
            if isinstance(node.op, ast.Mod):
                return left % right
        raise MartBuildError(f"unsupported expression node: {type(node).__name__}")

    value = evaluate(tree)
    if isinstance(value, pd.Series):
        return value
    return pd.Series(value, index=frame.index, dtype="Float64")
