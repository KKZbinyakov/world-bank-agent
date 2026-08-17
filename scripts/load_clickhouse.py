"""Load one processed World Bank run and optional DataLens marts into ClickHouse."""

from __future__ import annotations

import argparse
from pathlib import Path

from wb_insight.config import get_settings
from wb_insight.storage import ClickHouseRepository


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--mart-dir", type=Path)
    parser.add_argument("--batch-size", type=int, default=10_000)
    parser.add_argument("--ddl-dir", type=Path, default=Path("sql/ddl"))
    parser.add_argument("--marts-sql-dir", type=Path, default=Path("sql/marts"))
    args = parser.parse_args()

    settings = get_settings()
    with ClickHouseRepository.from_settings(settings) as repository:
        version = repository.version()
        print(f"Connected to ClickHouse {version}")

        ddl_files = repository.apply_sql_directory(args.ddl_dir)
        print(f"Applied DDL files: {len(ddl_files)}")

        # Validate/create SQL views before loading data. This prevents a bad mart
        # definition from leaving a newly loaded run behind as a partial success.
        mart_files = repository.apply_sql_directory(args.marts_sql_dir)
        print(f"Applied SQL marts: {len(mart_files)}")

        result = repository.load_processed_run(
            args.run_dir,
            mart_dir=args.mart_dir,
            batch_size=args.batch_size,
        )

    print("ClickHouse load completed:")
    print(f"  run_id: {result.run_id}")
    print(f"  countries: {result.countries}")
    print(f"  indicators: {result.indicators}")
    print(f"  observations: {result.observations}")
    if result.wide_rows is not None:
        print(f"  wide mart rows: {result.wide_rows}")
    if result.metric_catalog_rows is not None:
        print(f"  metric catalog rows: {result.metric_catalog_rows}")


if __name__ == "__main__":
    main()
