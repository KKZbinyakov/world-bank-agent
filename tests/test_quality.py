from __future__ import annotations

import pandas as pd

from wb_insight.quality.checks import run_indicator_checks, run_observation_checks


def _observations() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "source_id": 2,
                "indicator_code": indicator,
                "country_code": country,
                "year": 2024,
                "value": 1.0,
                "dimensions_json": "{}",
            }
            for country in ("DEU", "NLD")
            for indicator in ("NY.GDP.PCAP.CD", "SP.POP.TOTL")
        ]
    )


def _indicator_keys() -> set[tuple[int, str]]:
    return {(2, "NY.GDP.PCAP.CD"), (2, "SP.POP.TOTL")}


def test_observation_quality_passes_for_complete_unique_scope() -> None:
    report = run_observation_checks(
        _observations(),
        expected_country_codes={"DEU", "NLD"},
        expected_indicator_keys=_indicator_keys(),
        start_year=2024,
        end_year=2024,
    )

    assert report.failed_count == 0
    assert report.warning_count == 0
    assert report.passed_count == len(report.checks)


def test_missing_metric_values_are_warning_not_failure() -> None:
    frame = _observations()
    frame.loc[0, "value"] = None

    report = run_observation_checks(
        frame,
        expected_country_codes={"DEU", "NLD"},
        expected_indicator_keys=_indicator_keys(),
        start_year=2024,
        end_year=2024,
    )

    completeness = next(
        check for check in report.checks if check.name == "observations.value_completeness"
    )
    assert completeness.status == "warning"
    assert completeness.affected_rows == 1
    assert report.failed_count == 0


def test_duplicate_observation_key_is_failure() -> None:
    frame = pd.concat([_observations(), _observations().iloc[[0]]], ignore_index=True)

    report = run_observation_checks(
        frame,
        expected_country_codes={"DEU", "NLD"},
        expected_indicator_keys=_indicator_keys(),
        start_year=2024,
        end_year=2024,
    )

    unique_key = next(check for check in report.checks if check.name == "observations.unique_key")
    assert unique_key.status == "failed"
    assert report.has_failures is True


def test_missing_country_indicator_year_key_is_warning() -> None:
    frame = _observations().iloc[:-1].copy()

    report = run_observation_checks(
        frame,
        expected_country_codes={"DEU", "NLD"},
        expected_indicator_keys=_indicator_keys(),
        start_year=2024,
        end_year=2024,
    )

    coverage = next(check for check in report.checks if check.name == "observations.key_coverage")
    assert coverage.status == "warning"
    assert coverage.affected_rows == 1


def test_missing_semantic_unit_is_warning_not_failure() -> None:
    frame = pd.DataFrame(
        [
            {
                "source_id": 6,
                "indicator_code": "DT.DOD.DECT.CD",
                "indicator_name": "External debt",
                "unit": None,
                "category": "external_debt",
                "unit_source": "missing",
                "category_source": "world_bank_topic",
            }
        ]
    )

    report = run_indicator_checks(
        frame,
        expected_indicator_keys={(6, "DT.DOD.DECT.CD")},
    )

    unit_check = next(check for check in report.checks if check.name == "indicators.selected_units")
    assert unit_check.status == "warning"
    assert unit_check.affected_rows == 1
    assert report.failed_count == 0


def test_same_indicator_code_from_different_sources_is_distinguished() -> None:
    frame = pd.DataFrame(
        [
            {
                "source_id": 2,
                "indicator_code": "DUP.CODE",
                "country_code": "DEU",
                "year": 2024,
                "value": 1.0,
                "dimensions_json": "{}",
            },
            {
                "source_id": 6,
                "indicator_code": "DUP.CODE",
                "country_code": "DEU",
                "year": 2024,
                "value": 2.0,
                "dimensions_json": "{}",
            },
        ]
    )

    report = run_observation_checks(
        frame,
        expected_country_codes={"DEU"},
        expected_indicator_keys={(2, "DUP.CODE"), (6, "DUP.CODE")},
        start_year=2024,
        end_year=2024,
    )

    assert report.failed_count == 0
    assert report.warning_count == 0


def test_same_base_key_with_different_dimension_values_is_not_duplicate() -> None:
    frame = pd.DataFrame(
        [
            {
                "source_id": 6,
                "indicator_code": "DT.DOD.DECT.CD",
                "country_code": "ARG",
                "year": 2024,
                "value": 10.0,
                "dimensions_json": '{"Counterpart-Area":{"id":"WLD","value":"World"}}',
            },
            {
                "source_id": 6,
                "indicator_code": "DT.DOD.DECT.CD",
                "country_code": "ARG",
                "year": 2024,
                "value": 2.0,
                "dimensions_json": '{"Counterpart-Area":{"id":"001","value":"Austria"}}',
            },
        ]
    )

    report = run_observation_checks(
        frame,
        expected_country_codes={"ARG"},
        expected_indicator_keys={(6, "DT.DOD.DECT.CD")},
        start_year=2024,
        end_year=2024,
    )

    unique_key = next(check for check in report.checks if check.name == "observations.unique_key")
    assert unique_key.status == "passed"
    assert report.failed_count == 0
