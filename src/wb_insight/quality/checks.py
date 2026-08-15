"""Deterministic quality checks for normalized World Bank datasets."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

import pandas as pd

CheckStatus = Literal["passed", "warning", "failed"]
IndicatorKey = tuple[int, str]


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Result of one data-quality rule."""

    name: str
    status: CheckStatus
    message: str
    affected_rows: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class QualityReport:
    """Collection of quality-check results for one ingestion run."""

    checks: tuple[CheckResult, ...]

    @property
    def failed_count(self) -> int:
        return sum(check.status == "failed" for check in self.checks)

    @property
    def warning_count(self) -> int:
        return sum(check.status == "warning" for check in self.checks)

    @property
    def passed_count(self) -> int:
        return sum(check.status == "passed" for check in self.checks)

    @property
    def has_failures(self) -> bool:
        return self.failed_count > 0

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": {
                "passed": self.passed_count,
                "warnings": self.warning_count,
                "failed": self.failed_count,
            },
            "checks": [check.to_dict() for check in self.checks],
        }


class QualityCheckError(RuntimeError):
    """Raised when a normalized dataset violates a blocking quality rule."""

    def __init__(self, report: QualityReport) -> None:
        self.report = report
        failed_names = [check.name for check in report.checks if check.status == "failed"]
        super().__init__(f"data quality checks failed: {', '.join(failed_names)}")


def run_country_checks(
    frame: pd.DataFrame,
    *,
    expected_country_codes: set[str],
    aggregates_allowed: bool,
) -> QualityReport:
    """Validate normalized country metadata."""

    checks = [
        _check_nonempty(frame, dataset="countries"),
        _check_required_columns(
            frame,
            required={"country_code", "country_name", "is_aggregate"},
            dataset="countries",
        ),
    ]
    if _has_columns(frame, {"country_code"}):
        checks.extend(
            [
                _check_unique_keys(frame, keys=["country_code"], dataset="countries"),
                _check_expected_values_present(
                    frame,
                    column="country_code",
                    expected=expected_country_codes,
                    name="countries.scope_present",
                ),
            ]
        )
    if not aggregates_allowed and _has_columns(frame, {"is_aggregate"}):
        affected = int(frame["is_aggregate"].fillna(False).astype(bool).sum())
        checks.append(
            CheckResult(
                name="countries.no_aggregates",
                status="passed" if affected == 0 else "failed",
                message=(
                    "No aggregate entities are present."
                    if affected == 0
                    else f"Found {affected} aggregate entities after filtering."
                ),
                affected_rows=affected,
            )
        )
    return QualityReport(tuple(checks))


def run_indicator_checks(
    frame: pd.DataFrame,
    *,
    expected_indicator_keys: set[IndicatorKey],
) -> QualityReport:
    """Validate normalized indicator metadata using source-qualified keys."""

    semantic_columns = {"unit", "category", "unit_source", "category_source"}
    checks = [
        _check_nonempty(frame, dataset="indicators"),
        _check_required_columns(
            frame,
            required={"source_id", "indicator_code", "indicator_name"} | semantic_columns,
            dataset="indicators",
        ),
    ]
    if _has_columns(frame, {"source_id", "indicator_code"}):
        checks.extend(
            [
                _check_unique_keys(
                    frame,
                    keys=["source_id", "indicator_code"],
                    dataset="indicators",
                ),
                _check_expected_indicator_keys_present(
                    frame,
                    expected=expected_indicator_keys,
                    name="indicators.scope_present",
                ),
            ]
        )
    if _has_columns(frame, {"source_id", "indicator_code"} | semantic_columns):
        checks.extend(
            [
                _check_selected_semantics(
                    frame,
                    expected_indicator_keys=expected_indicator_keys,
                    column="unit",
                    missing_values={""},
                    name="indicators.selected_units",
                    label="unit metadata",
                ),
                _check_selected_semantics(
                    frame,
                    expected_indicator_keys=expected_indicator_keys,
                    column="category",
                    missing_values={"", "uncategorized"},
                    name="indicators.selected_categories",
                    label="category metadata",
                ),
            ]
        )
    return QualityReport(tuple(checks))


def run_observation_checks(
    frame: pd.DataFrame,
    *,
    expected_country_codes: set[str],
    expected_indicator_keys: set[IndicatorKey],
    start_year: int,
    end_year: int,
) -> QualityReport:
    """Validate long-format observations and report missing values as warnings."""

    required = {
        "source_id",
        "indicator_code",
        "country_code",
        "year",
        "value",
        "dimensions_json",
    }
    checks = [
        _check_nonempty(frame, dataset="observations"),
        _check_required_columns(frame, required=required, dataset="observations"),
    ]
    if not _has_columns(frame, required):
        return QualityReport(tuple(checks))

    checks.extend(
        [
            _check_unique_keys(
                frame,
                keys=[
                    "source_id",
                    "indicator_code",
                    "country_code",
                    "year",
                    "dimensions_json",
                ],
                dataset="observations",
            ),
            _check_allowed_values(
                frame,
                column="country_code",
                allowed=expected_country_codes,
                name="observations.country_scope",
            ),
            _check_allowed_indicator_keys(
                frame,
                allowed=expected_indicator_keys,
                name="observations.indicator_scope",
            ),
            _check_year_range(frame, start_year=start_year, end_year=end_year),
            _check_missing_values(frame, column="value"),
            _check_observation_coverage(
                frame,
                expected_country_codes=expected_country_codes,
                expected_indicator_keys=expected_indicator_keys,
                start_year=start_year,
                end_year=end_year,
            ),
        ]
    )
    return QualityReport(tuple(checks))


def combine_reports(*reports: QualityReport) -> QualityReport:
    """Combine several dataset reports into one run-level report."""

    return QualityReport(tuple(check for report in reports for check in report.checks))


def _check_nonempty(frame: pd.DataFrame, *, dataset: str) -> CheckResult:
    row_count = len(frame)
    return CheckResult(
        name=f"{dataset}.nonempty",
        status="passed" if row_count > 0 else "failed",
        message=f"Dataset contains {row_count} rows.",
        affected_rows=0 if row_count > 0 else 1,
    )


def _check_required_columns(
    frame: pd.DataFrame,
    *,
    required: set[str],
    dataset: str,
) -> CheckResult:
    missing = sorted(required - set(frame.columns))
    return CheckResult(
        name=f"{dataset}.required_columns",
        status="passed" if not missing else "failed",
        message=(
            "All required columns are present."
            if not missing
            else f"Missing required columns: {', '.join(missing)}."
        ),
        affected_rows=len(missing),
    )


def _check_unique_keys(frame: pd.DataFrame, *, keys: list[str], dataset: str) -> CheckResult:
    duplicate_mask = frame.duplicated(subset=keys, keep=False)
    affected = int(duplicate_mask.sum())
    return CheckResult(
        name=f"{dataset}.unique_key",
        status="passed" if affected == 0 else "failed",
        message=(
            f"Key {keys} is unique."
            if affected == 0
            else f"Found {affected} rows participating in duplicate keys {keys}."
        ),
        affected_rows=affected,
    )


def _check_expected_values_present(
    frame: pd.DataFrame,
    *,
    column: str,
    expected: set[str],
    name: str,
) -> CheckResult:
    actual = set(frame[column].dropna().astype(str))
    missing = sorted(expected - actual)
    return CheckResult(
        name=name,
        status="passed" if not missing else "failed",
        message=(
            "All configured values are present."
            if not missing
            else f"Configured values absent from dataset: {', '.join(missing)}."
        ),
        affected_rows=len(missing),
    )


def _check_expected_indicator_keys_present(
    frame: pd.DataFrame,
    *,
    expected: set[IndicatorKey],
    name: str,
) -> CheckResult:
    actual = _indicator_keys(frame)
    missing = sorted(expected - actual)
    formatted = [_format_indicator_key(key) for key in missing]
    return CheckResult(
        name=name,
        status="passed" if not missing else "failed",
        message=(
            "All configured source/indicator pairs are present."
            if not missing
            else "Configured source/indicator pairs absent from dataset: "
            + ", ".join(formatted)
            + "."
        ),
        affected_rows=len(missing),
    )


def _check_selected_semantics(
    frame: pd.DataFrame,
    *,
    expected_indicator_keys: set[IndicatorKey],
    column: str,
    missing_values: set[str],
    name: str,
    label: str,
) -> CheckResult:
    selected_mask = pd.Series(False, index=frame.index)
    for source_id, indicator_code in expected_indicator_keys:
        selected_mask |= pd.to_numeric(frame["source_id"], errors="coerce").eq(source_id) & frame[
            "indicator_code"
        ].astype(str).eq(indicator_code)
    selected = frame.loc[selected_mask, ["source_id", "indicator_code", column]].copy()
    normalized = selected[column].astype("string").str.strip().str.lower()
    normalized_missing_values = {value.lower() for value in missing_values}
    missing_mask = selected[column].isna() | normalized.isin(normalized_missing_values)
    affected_keys = sorted(
        {
            (int(str(row.source_id)), str(row.indicator_code))
            for row in selected.loc[missing_mask, ["source_id", "indicator_code"]].itertuples(
                index=False
            )
            if pd.notna(row.source_id)
        }
    )
    formatted = [_format_indicator_key(key) for key in affected_keys]
    return CheckResult(
        name=name,
        status="passed" if not affected_keys else "warning",
        message=(
            f"All selected indicators have {label}."
            if not affected_keys
            else f"Selected indicators missing reliable {label}: {', '.join(formatted)}."
        ),
        affected_rows=len(affected_keys),
    )


def _check_allowed_indicator_keys(
    frame: pd.DataFrame,
    *,
    allowed: set[IndicatorKey],
    name: str,
) -> CheckResult:
    actual = _indicator_keys(frame)
    unexpected = sorted(actual - allowed)
    formatted = [_format_indicator_key(key) for key in unexpected]
    return CheckResult(
        name=name,
        status="passed" if not unexpected else "failed",
        message=(
            "All source/indicator pairs belong to the configured scope."
            if not unexpected
            else "Unexpected source/indicator pairs found: " + ", ".join(formatted) + "."
        ),
        affected_rows=len(unexpected),
    )


def _check_allowed_values(
    frame: pd.DataFrame,
    *,
    column: str,
    allowed: set[str],
    name: str,
) -> CheckResult:
    actual = set(frame[column].dropna().astype(str))
    unexpected = sorted(actual - allowed)
    return CheckResult(
        name=name,
        status="passed" if not unexpected else "failed",
        message=(
            "All values belong to the configured scope."
            if not unexpected
            else f"Unexpected values found: {', '.join(unexpected)}."
        ),
        affected_rows=len(unexpected),
    )


def _check_year_range(frame: pd.DataFrame, *, start_year: int, end_year: int) -> CheckResult:
    numeric_years = pd.to_numeric(frame["year"], errors="coerce")
    invalid_mask = numeric_years.isna() | ~numeric_years.between(start_year, end_year)
    affected = int(invalid_mask.sum())
    return CheckResult(
        name="observations.year_range",
        status="passed" if affected == 0 else "failed",
        message=(
            f"All observation years are within {start_year}-{end_year}."
            if affected == 0
            else f"Found {affected} observations outside {start_year}-{end_year}."
        ),
        affected_rows=affected,
    )


def _check_missing_values(frame: pd.DataFrame, *, column: str) -> CheckResult:
    affected = int(frame[column].isna().sum())
    return CheckResult(
        name=f"observations.{column}_completeness",
        status="passed" if affected == 0 else "warning",
        message=(
            f"Column {column} has no missing values."
            if affected == 0
            else f"Column {column} contains {affected} missing values; they are kept as null."
        ),
        affected_rows=affected,
    )


def _check_observation_coverage(
    frame: pd.DataFrame,
    *,
    expected_country_codes: set[str],
    expected_indicator_keys: set[IndicatorKey],
    start_year: int,
    end_year: int,
) -> CheckResult:
    expected_rows = (
        len(expected_country_codes) * len(expected_indicator_keys) * (end_year - start_year + 1)
    )
    actual_rows = len(
        frame[["source_id", "country_code", "indicator_code", "year"]].drop_duplicates()
    )
    missing = max(expected_rows - actual_rows, 0)
    return CheckResult(
        name="observations.key_coverage",
        status="passed" if missing == 0 else "warning",
        message=(
            f"All {expected_rows} expected source/country/indicator/year keys are present."
            if missing == 0
            else (
                f"{missing} of {expected_rows} expected "
                "source/country/indicator/year keys are absent."
            )
        ),
        affected_rows=missing,
    )


def _indicator_keys(frame: pd.DataFrame) -> set[IndicatorKey]:
    keys: set[IndicatorKey] = set()
    for row in frame[["source_id", "indicator_code"]].dropna().itertuples(index=False):
        try:
            keys.add((int(str(row.source_id)), str(row.indicator_code)))
        except (TypeError, ValueError):
            continue
    return keys


def _format_indicator_key(key: IndicatorKey) -> str:
    source_id, indicator_code = key
    return f"{source_id}:{indicator_code}"


def _has_columns(frame: pd.DataFrame, columns: set[str]) -> bool:
    return columns.issubset(frame.columns)
