from __future__ import annotations

import pytest

from wb_insight.analytics import (
    AnalyticalError,
    TimeseriesPoint,
    calculate_correlation,
    calculate_trend,
    compare_countries,
)


def _point(
    country: str,
    year: int,
    value: float | None,
    *,
    code: str = "NY.GDP.PCAP.CD",
    alias: str = "gdp_per_capita_current_usd",
) -> TimeseriesPoint:
    names = {"DEU": "Germany", "NLD": "Netherlands", "POL": "Poland"}
    return TimeseriesPoint(
        run_id="run-1",
        source_id=2,
        indicator_code=code,
        indicator_alias=alias,
        indicator_name=code,
        unit="unit",
        display_unit="display",
        country_code=country,
        country_name=names[country],
        year=year,
        value=value,
        is_missing=value is None,
    )


def test_calculate_trend_returns_change_cagr_and_slope() -> None:
    result = calculate_trend(
        [
            _point("DEU", 2020, 100.0),
            _point("DEU", 2021, 110.0),
            _point("DEU", 2022, 121.0),
        ]
    )

    assert result.start_year == 2020
    assert result.end_year == 2022
    assert result.absolute_change == pytest.approx(21.0)
    assert result.percent_change == pytest.approx(21.0)
    assert result.cagr_percent == pytest.approx(10.0)
    assert result.average_annual_change == pytest.approx(10.5)
    assert result.linear_slope_per_year == pytest.approx(10.5)
    assert result.annualized_change_volatility_percent == pytest.approx(0.0)
    assert result.missing_count == 0
    assert len(result.evidence) == 3


def test_calculate_trend_reports_missing_year_and_invalid_cagr() -> None:
    result = calculate_trend(
        [
            _point("DEU", 2020, -10.0),
            _point("DEU", 2021, None),
            _point("DEU", 2022, 20.0),
        ]
    )

    assert result.missing_count == 1
    assert result.cagr_percent is None
    assert any("CAGR" in warning for warning in result.warnings)
    assert any("missing years" in warning for warning in result.warnings)


def test_calculate_trend_with_one_observation_returns_warning() -> None:
    result = calculate_trend([_point("DEU", 2024, 52_000.0)])

    assert result.observation_count == 1
    assert result.absolute_change is None
    assert "At least two" in result.warnings[0]


def test_calculate_trend_rejects_multiple_series() -> None:
    with pytest.raises(AnalyticalError, match="exactly one"):
        calculate_trend([_point("DEU", 2024, 1.0), _point("NLD", 2024, 2.0)])


def test_compare_countries_uses_latest_common_year_and_ranks_values() -> None:
    result = compare_countries(
        [
            _point("DEU", 2023, 50.0),
            _point("DEU", 2024, 52.0),
            _point("NLD", 2023, 60.0),
            _point("NLD", 2024, None),
            _point("POL", 2023, 30.0),
            _point("POL", 2024, 35.0),
        ]
    )

    assert result.year == 2023
    assert result.mean == pytest.approx(46.6666666667)
    assert result.median == pytest.approx(50.0)
    assert [entry.country_code for entry in result.entries] == ["NLD", "DEU", "POL"]
    assert [entry.rank for entry in result.entries] == [1, 2, 3]
    assert not result.missing_countries
    assert len(result.evidence) == 3


def test_compare_countries_explicit_year_reports_missing_country() -> None:
    result = compare_countries(
        [
            _point("DEU", 2024, 52.0),
            _point("NLD", 2024, None),
        ],
        year=2024,
    )

    assert result.year == 2024
    assert result.missing_countries == ("NLD",)
    assert len(result.entries) == 1
    assert "1 countries" in result.warnings[0]


def test_compare_countries_reports_no_common_year() -> None:
    result = compare_countries(
        [
            _point("DEU", 2023, 50.0),
            _point("NLD", 2024, 60.0),
        ]
    )

    assert result.year is None
    assert not result.entries
    assert result.missing_countries == ("DEU", "NLD")


def test_calculate_pearson_correlation_matches_country_year_pairs() -> None:
    x_points = [
        _point("DEU", 2023, 1.0),
        _point("DEU", 2024, 2.0),
        _point("NLD", 2023, 3.0),
        _point("NLD", 2024, 4.0),
    ]
    y_points = [
        _point("DEU", 2023, 2.0, code="SL.UEM.TOTL.ZS", alias="unemployment"),
        _point("DEU", 2024, 4.0, code="SL.UEM.TOTL.ZS", alias="unemployment"),
        _point("NLD", 2023, 6.0, code="SL.UEM.TOTL.ZS", alias="unemployment"),
        _point("NLD", 2024, 8.0, code="SL.UEM.TOTL.ZS", alias="unemployment"),
    ]

    result = calculate_correlation(x_points, y_points)

    assert result.coefficient == pytest.approx(1.0)
    assert result.sample_size == 4
    assert result.dropped_pairs == 0
    assert result.countries_used == ("DEU", "NLD")
    assert result.y_indicator_code == "SL.UEM.TOTL.ZS"
    assert len(result.pairs) == 4
    assert "does not establish causation" in result.warnings[0]
    assert any("Pooled" in warning for warning in result.warnings)


def test_calculate_spearman_correlation_supports_ties() -> None:
    x_points = [
        _point("DEU", 2022, 10.0),
        _point("DEU", 2023, 10.0),
        _point("DEU", 2024, 20.0),
    ]
    y_points = [
        _point("DEU", 2022, 1.0, code="Y", alias="y"),
        _point("DEU", 2023, 1.0, code="Y", alias="y"),
        _point("DEU", 2024, 2.0, code="Y", alias="y"),
    ]

    result = calculate_correlation(x_points, y_points, method="spearman")

    assert result.coefficient == pytest.approx(1.0)
    assert result.sample_size == 3


def test_calculate_correlation_reports_insufficient_sample_and_drops_nulls() -> None:
    x_points = [
        _point("DEU", 2023, 1.0),
        _point("DEU", 2024, 2.0),
        _point("NLD", 2024, 3.0),
    ]
    y_points = [
        _point("DEU", 2023, 2.0, code="Y", alias="y"),
        _point("DEU", 2024, None, code="Y", alias="y"),
    ]

    result = calculate_correlation(x_points, y_points, min_observations=3)

    assert result.coefficient is None
    assert result.sample_size == 1
    assert result.dropped_pairs == 2
    assert any("minimum is 3" in warning for warning in result.warnings)


def test_calculate_correlation_rejects_same_metric() -> None:
    points = [
        _point("DEU", 2022, 1.0),
        _point("DEU", 2023, 2.0),
        _point("DEU", 2024, 3.0),
    ]

    with pytest.raises(AnalyticalError, match="different metric"):
        calculate_correlation(points, points)


def test_calculate_correlation_rejects_unknown_method() -> None:
    x_points = [
        _point("DEU", 2022, 1.0),
        _point("DEU", 2023, 2.0),
        _point("DEU", 2024, 3.0),
    ]
    y_points = [
        _point("DEU", 2022, 2.0, code="Y", alias="y"),
        _point("DEU", 2023, 3.0, code="Y", alias="y"),
        _point("DEU", 2024, 4.0, code="Y", alias="y"),
    ]

    with pytest.raises(ValueError, match="unsupported correlation method"):
        calculate_correlation(x_points, y_points, method="kendall")  # type: ignore[arg-type]
