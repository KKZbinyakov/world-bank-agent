"""Pure deterministic analytical calculations over citable time-series points."""

from __future__ import annotations

import math
from collections.abc import Sequence
from itertools import pairwise
from statistics import fmean, median, pstdev

from wb_insight.analytics.models import (
    AnalyticalError,
    CorrelationMethod,
    CorrelationPair,
    CorrelationResult,
    CountryComparisonEntry,
    CountryComparisonResult,
    TimeseriesPoint,
    TrendResult,
)


def calculate_trend(points: Sequence[TimeseriesPoint]) -> TrendResult:
    """Calculate transparent trend statistics for exactly one country series."""

    series = _one_series(points)
    ordered = sorted(series, key=lambda point: point.year)
    by_year = _unique_year_points(ordered)
    non_null = [point for point in by_year.values() if point.value is not None]
    template = ordered[0]
    run_ids = {point.run_id for point in ordered}
    first_year: int | None
    last_year: int | None
    first_point: TimeseriesPoint | None
    last_point: TimeseriesPoint | None

    if non_null:
        first_year = min(point.year for point in non_null)
        last_year = max(point.year for point in non_null)
        first_point = by_year[first_year]
        last_point = by_year[last_year]
        expected_count = last_year - first_year + 1
    else:
        first_year = last_year = None
        first_point = last_point = None
        expected_count = len(by_year)

    warnings: list[str] = []
    if len(non_null) < 2:
        warnings.append("At least two non-null observations are required for a trend.")
        return TrendResult(
            run_id=next(iter(run_ids)) if len(run_ids) == 1 else None,
            country_code=template.country_code,
            source_id=template.source_id,
            indicator_code=template.indicator_code,
            indicator_alias=template.indicator_alias,
            unit=template.unit,
            display_unit=template.display_unit,
            dimensions_json=template.dimensions_json,
            start_year=first_year,
            end_year=last_year,
            start_value=first_point.value if first_point else None,
            end_value=last_point.value if last_point else None,
            observation_count=len(non_null),
            missing_count=max(expected_count - len(non_null), 0),
            evidence=tuple(ordered),
            warnings=tuple(warnings),
        )

    assert first_point is not None and first_point.value is not None
    assert last_point is not None and last_point.value is not None
    year_span = last_point.year - first_point.year
    absolute_change = last_point.value - first_point.value
    percent_change: float | None = None
    cagr_percent: float | None = None
    average_annual_change: float | None = None

    if first_point.value == 0:
        warnings.append("Percent change is undefined because the starting value is zero.")
    else:
        percent_change = absolute_change / abs(first_point.value) * 100

    if year_span > 0:
        average_annual_change = absolute_change / year_span
        if first_point.value > 0 and last_point.value >= 0:
            cagr_percent = ((last_point.value / first_point.value) ** (1 / year_span) - 1) * 100
        else:
            warnings.append("CAGR requires a positive starting value and a non-negative end value.")
    else:
        warnings.append("Trend period has zero year span.")

    xs = [float(point.year) for point in non_null]
    ys = [float(point.value) for point in non_null if point.value is not None]
    slope = _linear_slope(xs, ys)
    annualized_changes = _annualized_percentage_changes(non_null)
    volatility = pstdev(annualized_changes) if len(annualized_changes) >= 2 else None
    if expected_count > len(non_null):
        warnings.append(
            f"The trend contains {expected_count - len(non_null)} missing years between endpoints."
        )

    return TrendResult(
        run_id=next(iter(run_ids)) if len(run_ids) == 1 else None,
        country_code=template.country_code,
        source_id=template.source_id,
        indicator_code=template.indicator_code,
        indicator_alias=template.indicator_alias,
        unit=template.unit,
        display_unit=template.display_unit,
        dimensions_json=template.dimensions_json,
        start_year=first_point.year,
        end_year=last_point.year,
        start_value=first_point.value,
        end_value=last_point.value,
        observation_count=len(non_null),
        missing_count=max(expected_count - len(non_null), 0),
        absolute_change=absolute_change,
        percent_change=percent_change,
        cagr_percent=cagr_percent,
        average_annual_change=average_annual_change,
        linear_slope_per_year=slope,
        annualized_change_volatility_percent=volatility,
        evidence=tuple(ordered),
        warnings=tuple(warnings),
    )


def compare_countries(
    points: Sequence[TimeseriesPoint],
    *,
    year: int | None = None,
    descending: bool = True,
) -> CountryComparisonResult:
    """Compare countries for one metric using an explicit or latest common year."""

    series = _one_metric(points)
    template = series[0]
    countries = sorted({point.country_code for point in series})
    by_country_year: dict[tuple[str, int], TimeseriesPoint] = {}
    for point in series:
        key = (point.country_code, point.year)
        if key in by_country_year:
            raise AnalyticalError(
                "country comparison received duplicate country/year rows; "
                "select one dimension slice"
            )
        by_country_year[key] = point

    comparison_year = year
    if comparison_year is None:
        years = sorted({series_point.year for series_point in series}, reverse=True)
        for candidate_year in years:
            has_all_values = True
            for country in countries:
                candidate_point = by_country_year.get((country, candidate_year))
                if candidate_point is None or candidate_point.value is None:
                    has_all_values = False
                    break
            if has_all_values:
                comparison_year = candidate_year
                break

    warnings: list[str] = []
    if comparison_year is None:
        warnings.append("No common non-null year exists for all countries.")
        return CountryComparisonResult(
            run_id=_single_run_id(series),
            source_id=template.source_id,
            indicator_code=template.indicator_code,
            indicator_alias=template.indicator_alias,
            unit=template.unit,
            display_unit=template.display_unit,
            dimensions_json=template.dimensions_json,
            year=None,
            descending=descending,
            mean=None,
            median=None,
            entries=(),
            evidence=(),
            missing_countries=tuple(countries),
            warnings=tuple(warnings),
        )

    available: list[tuple[TimeseriesPoint, float]] = []
    evidence_points: list[TimeseriesPoint] = []
    missing_values: list[str] = []
    for country in countries:
        selected_point = by_country_year.get((country, comparison_year))
        if selected_point is not None:
            evidence_points.append(selected_point)
        if selected_point is None or selected_point.value is None:
            missing_values.append(country)
        else:
            available.append((selected_point, selected_point.value))
    missing = tuple(missing_values)
    if not available:
        warnings.append(f"No non-null values are available for {comparison_year}.")
        return CountryComparisonResult(
            run_id=_single_run_id(series),
            source_id=template.source_id,
            indicator_code=template.indicator_code,
            indicator_alias=template.indicator_alias,
            unit=template.unit,
            display_unit=template.display_unit,
            dimensions_json=template.dimensions_json,
            year=comparison_year,
            descending=descending,
            mean=None,
            median=None,
            entries=(),
            evidence=tuple(evidence_points),
            missing_countries=missing,
            warnings=tuple(warnings),
        )

    values = [value for _, value in available]
    mean_value = fmean(values)
    median_value = float(median(values))
    ordered = sorted(available, key=lambda item: item[1], reverse=descending)
    distinct_values = set(values)
    rank_by_value = {
        value: 1 + sum(other > value if descending else other < value for other in distinct_values)
        for value in values
    }
    entries = tuple(
        CountryComparisonEntry(
            country_code=point.country_code,
            country_name=point.country_name,
            year=comparison_year,
            value=value,
            rank=rank_by_value[value],
            difference_from_mean=value - mean_value,
            difference_from_median=value - median_value,
            percent_difference_from_median=(
                None if median_value == 0 else (value - median_value) / abs(median_value) * 100
            ),
        )
        for point, value in ordered
    )
    if missing:
        warnings.append(f"{len(missing)} countries have no value for {comparison_year}.")
    if median_value == 0:
        warnings.append("Percent difference from median is undefined because median equals zero.")

    return CountryComparisonResult(
        run_id=_single_run_id(series),
        source_id=template.source_id,
        indicator_code=template.indicator_code,
        indicator_alias=template.indicator_alias,
        unit=template.unit,
        display_unit=template.display_unit,
        dimensions_json=template.dimensions_json,
        year=comparison_year,
        descending=descending,
        mean=mean_value,
        median=median_value,
        entries=entries,
        evidence=tuple(point for point, _ in ordered),
        missing_countries=missing,
        warnings=tuple(warnings),
    )


def calculate_correlation(
    x_points: Sequence[TimeseriesPoint],
    y_points: Sequence[TimeseriesPoint],
    *,
    method: CorrelationMethod = "pearson",
    min_observations: int = 3,
) -> CorrelationResult:
    """Correlate two metric series matched by country and year."""

    if method not in {"pearson", "spearman"}:
        raise ValueError(f"unsupported correlation method: {method}")
    if min_observations < 3:
        raise ValueError("min_observations must be at least 3")
    x_series = _one_metric(x_points)
    y_series = _one_metric(y_points)
    x_template = x_series[0]
    y_template = y_series[0]
    if (
        x_template.source_id,
        x_template.indicator_code,
        x_template.dimensions_json,
    ) == (
        y_template.source_id,
        y_template.indicator_code,
        y_template.dimensions_json,
    ):
        raise AnalyticalError("correlation requires two different metric series")

    x_by_key = _unique_pair_points(x_series)
    y_by_key = _unique_pair_points(y_series)
    union_keys = set(x_by_key) | set(y_by_key)
    matched: list[tuple[TimeseriesPoint, TimeseriesPoint, float, float]] = []
    for key in sorted(set(x_by_key) & set(y_by_key)):
        left = x_by_key[key]
        right = y_by_key[key]
        if left.value is not None and right.value is not None:
            matched.append((left, right, left.value, right.value))
    x_values = [x_value for _, _, x_value, _ in matched]
    y_values = [y_value for _, _, _, y_value in matched]
    warnings = ["Correlation describes association and does not establish causation."]
    matched_countries = {left.country_code for left, _, _, _ in matched}
    matched_years = {left.year for left, _, _, _ in matched}
    has_multiple_countries = len(matched_countries) > 1
    has_multiple_years = len(matched_years) > 1
    if has_multiple_countries and has_multiple_years:
        warnings.append(
            "Pooled country-year correlation does not control for country or time effects."
        )

    coefficient: float | None = None
    if len(matched) < min_observations:
        warnings.append(
            f"Only {len(matched)} matched observations are available; "
            f"minimum is {min_observations}."
        )
    else:
        values_x = _average_ranks(x_values) if method == "spearman" else x_values
        values_y = _average_ranks(y_values) if method == "spearman" else y_values
        coefficient = _pearson(values_x, values_y)
        if coefficient is None:
            warnings.append(
                "Correlation is undefined because at least one series has zero variance."
            )

    return CorrelationResult(
        method=method,
        x_source_id=x_template.source_id,
        x_indicator_code=x_template.indicator_code,
        x_indicator_alias=x_template.indicator_alias,
        x_unit=x_template.unit,
        x_display_unit=x_template.display_unit,
        x_dimensions_json=x_template.dimensions_json,
        y_source_id=y_template.source_id,
        y_indicator_code=y_template.indicator_code,
        y_indicator_alias=y_template.indicator_alias,
        y_unit=y_template.unit,
        y_display_unit=y_template.display_unit,
        y_dimensions_json=y_template.dimensions_json,
        coefficient=coefficient,
        sample_size=len(matched),
        dropped_pairs=len(union_keys) - len(matched),
        countries_used=tuple(sorted(matched_countries)),
        years_used=tuple(sorted(matched_years)),
        run_id=_single_run_id([*x_series, *y_series]),
        pairs=tuple(
            CorrelationPair(
                country_code=left.country_code,
                year=left.year,
                x_value=x_value,
                y_value=y_value,
            )
            for left, _, x_value, y_value in matched
        ),
        warnings=tuple(warnings),
    )


def _one_series(points: Sequence[TimeseriesPoint]) -> list[TimeseriesPoint]:
    if not points:
        raise AnalyticalError("at least one time-series point is required")
    identities = {point.series_key for point in points}
    if len(identities) != 1:
        raise AnalyticalError("trend calculation requires exactly one country/metric series")
    return list(points)


def _one_metric(points: Sequence[TimeseriesPoint]) -> list[TimeseriesPoint]:
    if not points:
        raise AnalyticalError("at least one time-series point is required")
    identities = {
        (point.source_id, point.indicator_code, point.dimensions_json) for point in points
    }
    if len(identities) != 1:
        raise AnalyticalError("calculation requires exactly one metric/dimension series")
    return list(points)


def _unique_year_points(points: Sequence[TimeseriesPoint]) -> dict[int, TimeseriesPoint]:
    by_year: dict[int, TimeseriesPoint] = {}
    for point in points:
        if point.year in by_year:
            raise AnalyticalError("time series contains duplicate years")
        by_year[point.year] = point
    return by_year


def _unique_pair_points(
    points: Sequence[TimeseriesPoint],
) -> dict[tuple[str, int], TimeseriesPoint]:
    result: dict[tuple[str, int], TimeseriesPoint] = {}
    for point in points:
        key = (point.country_code, point.year)
        if key in result:
            raise AnalyticalError(
                "correlation input contains duplicate country/year rows; select one dimension slice"
            )
        result[key] = point
    return result


def _single_run_id(points: Sequence[TimeseriesPoint]) -> str | None:
    run_ids = {point.run_id for point in points}
    return next(iter(run_ids)) if len(run_ids) == 1 else None


def _linear_slope(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    x_mean = fmean(xs)
    y_mean = fmean(ys)
    denominator = sum((value - x_mean) ** 2 for value in xs)
    if denominator == 0:
        return None
    return sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=True)) / denominator


def _annualized_percentage_changes(points: Sequence[TimeseriesPoint]) -> list[float]:
    changes: list[float] = []
    ordered = sorted(points, key=lambda point: point.year)
    for previous, current in pairwise(ordered):
        if previous.value is None or current.value is None:
            continue
        year_gap = current.year - previous.year
        if year_gap <= 0 or previous.value <= 0 or current.value < 0:
            continue
        changes.append(((current.value / previous.value) ** (1 / year_gap) - 1) * 100)
    return changes


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) != len(ys) or not xs:
        return None
    x_mean = fmean(xs)
    y_mean = fmean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=True))
    x_sum = sum((x - x_mean) ** 2 for x in xs)
    y_sum = sum((y - y_mean) ** 2 for y in ys)
    denominator = math.sqrt(x_sum * y_sum)
    if denominator == 0:
        return None
    return max(-1.0, min(1.0, numerator / denominator))


def _average_ranks(values: Sequence[float]) -> list[float]:
    positions: dict[float, list[int]] = {}
    for index, value in enumerate(sorted(values), start=1):
        positions.setdefault(value, []).append(index)
    rank_by_value = {value: fmean(ranks) for value, ranks in positions.items()}
    return [rank_by_value[value] for value in values]
