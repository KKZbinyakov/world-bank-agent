"""Smoke-test a populated local or managed ClickHouse database."""

from __future__ import annotations

from wb_insight.config import get_settings
from wb_insight.storage import ClickHouseRepository


def main() -> None:
    settings = get_settings()

    with ClickHouseRepository.from_settings(settings) as repository:
        print(f"ClickHouse version: {repository.version()}")

        checks = {
            "runs": """
                SELECT count()
                FROM etl_run
                WHERE status = 'loaded'
            """,
            "countries": """
                SELECT uniqExact(country_code)
                FROM mart_indicator_timeseries
            """,
            "indicators": """
                SELECT uniqExact((source_id, indicator_code))
                FROM mart_indicator_timeseries
            """,
            "observations": """
                SELECT count()
                FROM mart_indicator_timeseries
            """,
            "wide_rows": """
                SELECT count()
                FROM mart_country_year_wide
            """,
            "metric_catalog_rows": """
                SELECT count()
                FROM mart_metric_catalog
            """,
        }

        for name, query in checks.items():
            value = repository.scalar(query)
            print(f"{name}: {value}")


if __name__ == "__main__":
    main()
