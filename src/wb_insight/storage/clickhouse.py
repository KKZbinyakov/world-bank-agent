"""ClickHouse persistence for Silver data and DataLens Gold marts."""

from __future__ import annotations

import importlib
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

import pandas as pd

from wb_insight.config import AppSettings
from wb_insight.storage.schemas import (
    COUNTRY_TABLE_COLUMNS,
    INDICATOR_TABLE_COLUMNS,
    OBSERVATION_TABLE_COLUMNS,
    dynamic_table_ddl,
    ensure_columns,
    ensure_safe_identifier,
)

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_=.-]+$")


class QueryResult(Protocol):
    """Minimal result interface used from clickhouse-connect."""

    @property
    def result_set(self) -> list[list[Any] | tuple[Any, ...]]: ...


class ClickHouseClient(Protocol):
    """Small protocol that keeps storage code independently unit-testable."""

    def command(self, query: str) -> Any: ...

    def query(self, query: str) -> QueryResult: ...

    def insert(
        self,
        table: str,
        data: Sequence[Sequence[Any]],
        *,
        column_names: Sequence[str],
    ) -> Any: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class ClickHouseLoadResult:
    """Summary of one processed run loaded into ClickHouse."""

    run_id: str
    countries: int
    indicators: int
    observations: int
    wide_rows: int | None = None
    metric_catalog_rows: int | None = None


class ClickHouseRepository:
    """Read/write wrapper around the official clickhouse-connect client."""

    def __init__(self, client: ClickHouseClient, *, database: str) -> None:
        self._client = client
        self.database = ensure_safe_identifier(database)

    @classmethod
    def from_settings(cls, settings: AppSettings) -> ClickHouseRepository:
        """Create a ClickHouse client from application settings."""

        if not settings.clickhouse_host:
            raise ValueError("CLICKHOUSE_HOST is required")
        if not settings.clickhouse_user:
            raise ValueError("CLICKHOUSE_USER is required")
        if not settings.clickhouse_password:
            raise ValueError("CLICKHOUSE_PASSWORD is required")

        module = importlib.import_module("clickhouse_connect")
        get_client = cast(Any, module).get_client
        client = cast(
            ClickHouseClient,
            get_client(
                host=settings.clickhouse_host,
                port=settings.clickhouse_port,
                username=settings.clickhouse_user,
                password=settings.clickhouse_password,
                database=settings.clickhouse_database,
                secure=settings.clickhouse_secure,
            ),
        )
        return cls(client, database=settings.clickhouse_database)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ClickHouseRepository:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def scalar(self, query: str) -> Any:
        """Return the first scalar from a read-only query."""

        rows = self._client.query(query).result_set
        if not rows or not rows[0]:
            raise RuntimeError("ClickHouse scalar query returned no rows")
        return rows[0][0]

    def version(self) -> str:
        """Return server version and simultaneously validate connectivity."""

        return str(self.scalar("SELECT version()"))

    def apply_sql_directory(self, directory: Path) -> list[Path]:
        """Execute sorted `.sql` files, one statement per file."""

        if not directory.is_dir():
            raise FileNotFoundError(f"SQL directory not found: {directory}")
        applied: list[Path] = []
        for path in sorted(directory.glob("*.sql")):
            statement = path.read_text(encoding="utf-8").strip()
            if not statement:
                continue
            self._client.command(statement)
            applied.append(path)
        return applied

    def load_processed_run(
        self,
        run_dir: Path,
        *,
        mart_dir: Path | None = None,
        batch_size: int = 10_000,
    ) -> ClickHouseLoadResult:
        """Load one immutable processed run and optionally its configured Gold marts."""

        countries = _read_required_parquet(run_dir / "countries.parquet")
        indicators = _read_required_parquet(run_dir / "indicators.parquet")
        observations = _read_required_parquet(run_dir / "observations.parquet")

        ensure_columns(countries, COUNTRY_TABLE_COLUMNS, "countries")
        ensure_columns(indicators, INDICATOR_TABLE_COLUMNS, "indicators")
        ensure_columns(observations, OBSERVATION_TABLE_COLUMNS, "observations")

        run_id = _single_run_id(countries, indicators, observations)
        self.delete_run(run_id)

        self.insert_dataframe(
            "dim_country", countries, COUNTRY_TABLE_COLUMNS, batch_size=batch_size
        )
        self.insert_dataframe(
            "dim_indicator",
            indicators,
            INDICATOR_TABLE_COLUMNS,
            batch_size=batch_size,
        )
        self.insert_dataframe(
            "fact_observation",
            observations,
            OBSERVATION_TABLE_COLUMNS,
            batch_size=batch_size,
        )

        wide_rows: int | None = None
        catalog_rows: int | None = None
        if mart_dir is not None:
            wide_path = mart_dir / "worldbank_datalens_wide.csv"
            catalog_path = mart_dir / "worldbank_metric_catalog.csv"
            if wide_path.exists():
                wide = pd.read_csv(wide_path, low_memory=False)
                wide.insert(0, "run_id", run_id)
                self.replace_dynamic_table(
                    "mart_country_year_wide",
                    wide,
                    order_by=("country_code", "year"),
                    batch_size=batch_size,
                )
                wide_rows = len(wide)
            if catalog_path.exists():
                catalog = pd.read_csv(catalog_path, low_memory=False)
                catalog.insert(0, "run_id", run_id)
                self.replace_dynamic_table(
                    "mart_metric_catalog",
                    catalog,
                    order_by=("source_id", "indicator_code"),
                    batch_size=batch_size,
                )
                catalog_rows = len(catalog)

        loaded_at = _latest_loaded_at(countries, indicators, observations)
        self.insert_etl_run(
            run_id=run_id,
            source_path=str(run_dir),
            countries=len(countries),
            indicators=len(indicators),
            observations=len(observations),
            wide_rows=wide_rows,
            loaded_at=loaded_at,
        )
        return ClickHouseLoadResult(
            run_id=run_id,
            countries=len(countries),
            indicators=len(indicators),
            observations=len(observations),
            wide_rows=wide_rows,
            metric_catalog_rows=catalog_rows,
        )

    def insert_dataframe(
        self,
        table: str,
        frame: pd.DataFrame,
        columns: Sequence[str] | None = None,
        *,
        batch_size: int = 10_000,
    ) -> None:
        """Insert a DataFrame using bounded batches and Python-native nulls."""

        safe_table = ensure_safe_identifier(table)
        selected = list(columns or [str(column) for column in frame.columns])
        ensure_columns(frame, selected, safe_table)
        if batch_size < 1:
            raise ValueError("batch_size must be positive")

        batch: list[tuple[Any, ...]] = []
        for row in frame[selected].itertuples(index=False, name=None):
            batch.append(tuple(_python_value(value) for value in row))
            if len(batch) >= batch_size:
                self._client.insert(safe_table, batch, column_names=selected)
                batch = []
        if batch:
            self._client.insert(safe_table, batch, column_names=selected)

    def replace_dynamic_table(
        self,
        table: str,
        frame: pd.DataFrame,
        *,
        order_by: Sequence[str],
        batch_size: int = 10_000,
    ) -> None:
        """Atomically replace the current DataLens-oriented dynamic table."""

        safe_table = ensure_safe_identifier(table)
        staging = ensure_safe_identifier(f"{safe_table}__staging")
        backup = ensure_safe_identifier(f"{safe_table}__old")

        self._client.command(f"DROP TABLE IF EXISTS `{staging}`")
        ddl = dynamic_table_ddl(staging, frame, order_by=order_by)
        self._client.command(ddl)
        self.insert_dataframe(staging, frame, batch_size=batch_size)

        self._client.command(f"DROP TABLE IF EXISTS `{backup}`")
        exists = int(self._client.query(f"EXISTS TABLE `{safe_table}`").result_set[0][0])
        if exists:
            self._client.command(
                f"RENAME TABLE `{safe_table}` TO `{backup}`, `{staging}` TO `{safe_table}`"
            )
            self._client.command(f"DROP TABLE IF EXISTS `{backup}`")
        else:
            self._client.command(f"RENAME TABLE `{staging}` TO `{safe_table}`")

    def delete_run(self, run_id: str) -> None:
        """Remove an existing copy of one run so reloads are idempotent."""

        safe_run_id = _safe_run_id(run_id)
        for table in ("fact_observation", "dim_indicator", "dim_country", "etl_run"):
            self._client.command(
                f"ALTER TABLE `{table}` DELETE WHERE run_id = '{safe_run_id}' "
                "SETTINGS mutations_sync = 1"
            )

    def insert_etl_run(
        self,
        *,
        run_id: str,
        source_path: str,
        countries: int,
        indicators: int,
        observations: int,
        wide_rows: int | None,
        loaded_at: datetime,
    ) -> None:
        """Mark a run as fully loaded only after all data writes succeed."""

        row = [
            run_id,
            loaded_at.astimezone(UTC),
            source_path,
            countries,
            indicators,
            observations,
            wide_rows,
            "loaded",
        ]
        self._client.insert(
            "etl_run",
            [row],
            column_names=[
                "run_id",
                "loaded_at",
                "source_path",
                "countries",
                "indicators",
                "observations",
                "wide_rows",
                "status",
            ],
        )


def _read_required_parquet(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f"processed artifact not found: {path}")
    return pd.read_parquet(path)


def _single_run_id(*frames: pd.DataFrame) -> str:
    values: set[str] = set()
    for frame in frames:
        if "run_id" not in frame.columns:
            raise ValueError("processed artifact is missing run_id")
        values.update(str(value) for value in frame["run_id"].dropna().unique())
    if len(values) != 1:
        raise ValueError(
            f"processed artifacts must contain exactly one run_id, got {sorted(values)}"
        )
    return next(iter(values))


def _latest_loaded_at(*frames: pd.DataFrame) -> datetime:
    values: list[pd.Timestamp] = []
    for frame in frames:
        if "loaded_at" in frame.columns and not frame.empty:
            converted = pd.to_datetime(frame["loaded_at"], utc=True, errors="coerce").dropna()
            values.extend(converted.tolist())
    if not values:
        return datetime.now(UTC)
    return max(values).to_pydatetime()


def _safe_run_id(run_id: str) -> str:
    if not _RUN_ID_RE.fullmatch(run_id):
        raise ValueError(f"unsafe run_id: {run_id!r}")
    return run_id


def _python_value(value: object) -> Any:
    """Convert pandas/numpy scalars into values accepted by clickhouse-connect."""

    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    item = cast(Any, value).item if hasattr(value, "item") else None
    if callable(item):
        try:
            return item()
        except (TypeError, ValueError):
            pass
    return value
