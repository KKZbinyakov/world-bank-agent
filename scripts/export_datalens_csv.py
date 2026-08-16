"""Export any processed World Bank run to generic DataLens-ready CSV marts."""

from __future__ import annotations

import argparse
from pathlib import Path

from wb_insight.marts import MartBuildError, export_run_to_csv


def _csv_set(value: str | None, *, upper: bool = False) -> set[str] | None:
    if value is None:
        return None
    items = {item.strip() for item in value.split(",") if item.strip()}
    if upper:
        items = {item.upper() for item in items}
    return items or None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build universal long/wide CSV marts from a processed ingestion run."
    )
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("data/marts"))
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional YAML with column aliases, country labels and derived metrics.",
    )
    parser.add_argument(
        "--countries",
        default=None,
        help="Optional comma-separated ISO3 filter.",
    )
    parser.add_argument(
        "--indicators",
        default=None,
        help="Optional comma-separated aliases, raw codes or SOURCE_ID:CODE selectors.",
    )
    parser.add_argument("--start-year", type=int, default=None)
    parser.add_argument("--end-year", type=int, default=None)
    parser.add_argument(
        "--dimension-mode",
        choices=("columns", "error"),
        default="columns",
        help="columns preserves extra source dimensions in distinct wide columns.",
    )
    parser.add_argument(
        "--no-complete-grid",
        action="store_true",
        help="Do not fill missing country-year combinations in the wide mart.",
    )
    args = parser.parse_args()

    try:
        paths = export_run_to_csv(
            args.run_dir,
            args.output_dir,
            config_path=args.config,
            country_codes=_csv_set(args.countries, upper=True),
            indicator_selectors=_csv_set(args.indicators),
            start_year=args.start_year,
            end_year=args.end_year,
            dimension_mode=args.dimension_mode,
            complete_grid=not args.no_complete_grid,
        )
    except (MartBuildError, FileNotFoundError, OSError, ValueError) as exc:
        parser.exit(1, f"Mart export failed: {exc}\n")

    print("DataLens marts created:")
    for name, path in paths.items():
        print(f"  {name}: {path}")


if __name__ == "__main__":
    main()
