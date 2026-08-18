"""Read-only smoke test for the deterministic analytical core."""

from __future__ import annotations

import json

from wb_insight.analytics import (
    AnalyticalRepository,
    calculate_correlation,
    calculate_trend,
    compare_countries,
)
from wb_insight.config import get_settings


def main() -> None:
    settings = get_settings()
    countries = ["DEU", "FRA", "POL"]
    with AnalyticalRepository.from_settings(settings) as repository:
        gdp = repository.get_timeseries(
            countries=countries,
            metrics=["2:NY.GDP.PCAP.CD"],
            start_year=2015,
            end_year=2024,
        )
        unemployment = repository.get_timeseries(
            countries=countries,
            metrics=["2:SL.UEM.TOTL.ZS"],
            start_year=2015,
            end_year=2024,
        )
        snapshot = repository.get_country_snapshot(
            countries=countries,
            metrics=["2:NY.GDP.PCAP.CD"],
            mode="common_year",
        )
        quality = repository.get_data_quality(
            countries=countries,
            metrics=["2:NY.GDP.PCAP.CD", "2:SL.UEM.TOTL.ZS"],
        )

    deu_gdp = [point for point in gdp.points if point.country_code == "DEU"]
    output = {
        "countries": countries,
        "timeseries_points": len(gdp.points),
        "timeseries_warnings": list(gdp.warnings),
        "snapshot_year": snapshot.comparison_year,
        "snapshot_points": len(snapshot.points),
        "snapshot_warnings": list(snapshot.warnings),
        "trend": calculate_trend(deu_gdp).model_dump(mode="json"),
        "comparison": compare_countries(gdp.points).model_dump(mode="json"),
        "correlation": calculate_correlation(
            gdp.points,
            unemployment.points,
            min_observations=3,
        ).model_dump(mode="json"),
        "quality_rows": len(quality.entries),
        "quality_warnings": list(quality.warnings),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
