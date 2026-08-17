from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from wb_insight.storage.clickhouse import ClickHouseRepository
from wb_insight.storage.schemas import dynamic_table_ddl


class FakeResult:
    def __init__(self, rows: list[list[Any] | tuple[Any, ...]]) -> None:
        self._rows = rows

    @property
    def result_set(self) -> list[list[Any] | tuple[Any, ...]]:
        return self._rows


class FakeClient:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.inserts: list[tuple[str, list[list[Any] | tuple[Any, ...]], list[str]]] = []
        self.tables: set[str] = set()
        self.closed = False

    def command(self, query: str) -> None:
        self.commands.append(query)
        if query.startswith("CREATE TABLE `"):
            table = query.split("`", maxsplit=2)[1]
            self.tables.add(table)
        elif query.startswith("DROP TABLE IF EXISTS `"):
            table = query.split("`", maxsplit=2)[1]
            self.tables.discard(table)
        elif query.startswith("RENAME TABLE"):
            # Sufficient simulation for dynamic-table tests.
            tokens = query.replace(",", " ").split("`")
            names = [tokens[index] for index in range(1, len(tokens), 2)]
            if len(names) >= 2:
                self.tables.discard(names[0])
                self.tables.add(names[1])
            if len(names) >= 4:
                self.tables.discard(names[2])
                self.tables.add(names[3])

    def query(self, query: str) -> FakeResult:
        if query == "SELECT version()":
            return FakeResult([("26.7.1",)])
        if query.startswith("EXISTS TABLE `"):
            table = query.split("`", maxsplit=2)[1]
            return FakeResult([(1 if table in self.tables else 0,)])
        raise AssertionError(f"unexpected query: {query}")

    def insert(
        self,
        table: str,
        data: list[list[Any] | tuple[Any, ...]],
        *,
        column_names: list[str],
    ) -> None:
        self.inserts.append((table, list(data), list(column_names)))

    def close(self) -> None:
        self.closed = True


def test_dynamic_table_ddl_infers_nullable_types() -> None:
    frame = pd.DataFrame(
        {
            "country_code": ["DEU", "NLD"],
            "year": [2023, 2024],
            "value": [1.5, None],
            "label": ["x", None],
        }
    )

    ddl = dynamic_table_ddl(
        "mart_country_year_wide",
        frame,
        order_by=("country_code", "year"),
    )

    assert "`country_code` String" in ddl
    assert "`year` Int64" in ddl
    assert "`value` Nullable(Float64)" in ddl
    assert "`label` Nullable(String)" in ddl
    assert "ORDER BY (`country_code`, `year`)" in ddl


def test_repository_batches_and_normalizes_dataframe_values() -> None:
    client = FakeClient()
    repository = ClickHouseRepository(client, database="wb_insight")
    frame = pd.DataFrame(
        {
            "country_code": ["DEU", "NLD", "POL"],
            "value": pd.Series([1.0, pd.NA, 3.0], dtype="Float64"),
            "loaded_at": pd.to_datetime(
                ["2026-08-16T10:00:00Z"] * 3,
                utc=True,
            ),
        }
    )

    repository.insert_dataframe("test_table", frame, batch_size=2)

    assert len(client.inserts) == 2
    assert client.inserts[0][0] == "test_table"
    assert client.inserts[0][2] == ["country_code", "value", "loaded_at"]
    assert client.inserts[0][1][1][1] is None
    assert isinstance(client.inserts[0][1][0][2], datetime)


def test_apply_sql_directory_uses_sorted_files(tmp_path: Path) -> None:
    client = FakeClient()
    repository = ClickHouseRepository(client, database="wb_insight")
    (tmp_path / "002_second.sql").write_text("SELECT 2", encoding="utf-8")
    (tmp_path / "001_first.sql").write_text("SELECT 1", encoding="utf-8")

    applied = repository.apply_sql_directory(tmp_path)

    assert [path.name for path in applied] == ["001_first.sql", "002_second.sql"]
    assert client.commands == ["SELECT 1", "SELECT 2"]


def test_replace_dynamic_table_uses_staging_table() -> None:
    client = FakeClient()
    client.tables.add("mart_country_year_wide")
    repository = ClickHouseRepository(client, database="wb_insight")
    frame = pd.DataFrame(
        {
            "run_id": ["run_1"],
            "country_code": ["DEU"],
            "year": [2024],
            "gdp": [1.0],
        }
    )

    repository.replace_dynamic_table(
        "mart_country_year_wide",
        frame,
        order_by=("country_code", "year"),
    )

    assert "mart_country_year_wide" in client.tables
    assert "mart_country_year_wide__staging" not in client.tables
    assert client.inserts[0][0] == "mart_country_year_wide__staging"
    assert any("RENAME TABLE" in command for command in client.commands)


def test_repository_version_and_close() -> None:
    client = FakeClient()
    repository = ClickHouseRepository(client, database="wb_insight")

    assert repository.version() == "26.7.1"
    repository.close()
    assert client.closed is True


def test_unsafe_dynamic_identifier_is_rejected() -> None:
    frame = pd.DataFrame({"country_code": ["DEU"]})
    with pytest.raises(ValueError, match="unsafe ClickHouse identifier"):
        dynamic_table_ddl("bad;drop", frame)


def _country_frame(run_id: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "country_code": "DEU",
                "iso2_code": "DE",
                "country_name": "Germany",
                "region_code": "ECS",
                "region_name": "Europe & Central Asia",
                "admin_region_code": "",
                "admin_region_name": "",
                "income_level_code": "HIC",
                "income_level_name": "High income",
                "lending_type_code": "LNX",
                "lending_type_name": "Not classified",
                "capital_city": "Berlin",
                "longitude": 13.4,
                "latitude": 52.5,
                "is_aggregate": False,
                "run_id": run_id,
                "loaded_at": pd.Timestamp("2026-08-16T10:00:00Z"),
            }
        ]
    )


def _indicator_frame(run_id: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "source_id": 2,
                "source_name": "World Development Indicators",
                "indicator_code": "NY.GDP.PCAP.CD",
                "indicator_name": "GDP per capita",
                "source_unit": None,
                "alias": "gdp_per_capita",
                "name_ru": "ВВП на душу населения",
                "category": "economy",
                "category_source": "registry",
                "role": "target",
                "unit": "current_usd_per_person",
                "display_unit": "US$ / person",
                "unit_source": "registry",
                "is_registered": True,
                "source_note": "",
                "source_organization": "",
                "topic_ids": "3",
                "topic_names": "Economy & Growth",
                "run_id": run_id,
                "loaded_at": pd.Timestamp("2026-08-16T10:00:00Z"),
            }
        ]
    )


def _observation_frame(run_id: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "source_id": 2,
                "indicator_code": "NY.GDP.PCAP.CD",
                "indicator_name": "GDP per capita",
                "indicator_alias": "gdp_per_capita",
                "indicator_name_ru": "ВВП на душу населения",
                "indicator_category": "economy",
                "category_source": "registry",
                "indicator_role": "target",
                "country_code": "DEU",
                "country_name": "Germany",
                "year": 2024,
                "value": 1.0,
                "dimensions_json": "{}",
                "dimension_count": 0,
                "source_unit": None,
                "unit": "current_usd_per_person",
                "display_unit": "US$ / person",
                "unit_source": "registry",
                "is_registered": True,
                "observation_status": "",
                "decimal_scale": 1,
                "is_missing": False,
                "run_id": run_id,
                "loaded_at": pd.Timestamp("2026-08-16T10:00:01Z"),
            }
        ]
    )


def test_load_processed_run_loads_silver_and_gold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = "run_20260816"
    run_dir = tmp_path / f"run_id={run_id}"
    run_dir.mkdir()
    for name in ("countries.parquet", "indicators.parquet", "observations.parquet"):
        (run_dir / name).touch()

    frames = {
        "countries.parquet": _country_frame(run_id),
        "indicators.parquet": _indicator_frame(run_id),
        "observations.parquet": _observation_frame(run_id),
    }
    monkeypatch.setattr(
        "wb_insight.storage.clickhouse.pd.read_parquet",
        lambda path: frames[Path(path).name].copy(),
    )

    mart_dir = tmp_path / "marts"
    mart_dir.mkdir()
    pd.DataFrame({"country_code": ["DEU"], "year": [2024], "gdp_per_capita": [1.0]}).to_csv(
        mart_dir / "worldbank_datalens_wide.csv", index=False
    )
    pd.DataFrame(
        {
            "source_id": [2],
            "indicator_code": ["NY.GDP.PCAP.CD"],
            "wide_column": ["gdp_per_capita"],
        }
    ).to_csv(mart_dir / "worldbank_metric_catalog.csv", index=False)

    client = FakeClient()
    repository = ClickHouseRepository(client, database="wb_insight")
    result = repository.load_processed_run(run_dir, mart_dir=mart_dir, batch_size=1)

    assert result.run_id == run_id
    assert result.countries == 1
    assert result.indicators == 1
    assert result.observations == 1
    assert result.wide_rows == 1
    assert result.metric_catalog_rows == 1
    inserted_tables = [item[0] for item in client.inserts]
    assert "dim_country" in inserted_tables
    assert "dim_indicator" in inserted_tables
    assert "fact_observation" in inserted_tables
    assert "etl_run" in inserted_tables
    assert "mart_country_year_wide__staging" in inserted_tables
    assert "mart_metric_catalog__staging" in inserted_tables
    assert any("ALTER TABLE `fact_observation` DELETE" in command for command in client.commands)


def test_scalar_rejects_empty_result() -> None:
    class EmptyClient(FakeClient):
        def query(self, query: str) -> FakeResult:
            return FakeResult([])

    repository = ClickHouseRepository(EmptyClient(), database="wb_insight")
    with pytest.raises(RuntimeError, match="returned no rows"):
        repository.scalar("SELECT 1")


def test_delete_run_rejects_unsafe_run_id() -> None:
    repository = ClickHouseRepository(FakeClient(), database="wb_insight")
    with pytest.raises(ValueError, match="unsafe run_id"):
        repository.delete_run("bad'run")


def test_replace_dynamic_table_without_existing_table() -> None:
    client = FakeClient()
    repository = ClickHouseRepository(client, database="wb_insight")
    frame = pd.DataFrame({"country_code": ["DEU"], "year": [2024]})

    repository.replace_dynamic_table(
        "mart_country_year_wide",
        frame,
        order_by=("country_code", "year"),
    )

    assert "mart_country_year_wide" in client.tables
    rename_commands = [command for command in client.commands if command.startswith("RENAME TABLE")]
    assert len(rename_commands) == 1


def test_insert_dataframe_validates_batch_size() -> None:
    repository = ClickHouseRepository(FakeClient(), database="wb_insight")
    with pytest.raises(ValueError, match="batch_size"):
        repository.insert_dataframe("test_table", pd.DataFrame({"x": [1]}), batch_size=0)
