from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from wb_insight.config import AppSettings, load_application_config
from wb_insight.pipeline import _write_parquet, run_ingestion

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


def _fixture_records(name: str) -> list[dict[str, Any]]:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return payload[1]


class FakeWorldBankClient:
    def __init__(self) -> None:
        self.observation_calls: list[tuple[int | None, str]] = []

    def get_countries(self) -> list[dict[str, Any]]:
        return _fixture_records("countries_page_1.json")

    def get_indicators(self, source_id: int | None = None) -> list[dict[str, Any]]:
        return _fixture_records("indicators_page_1.json")

    def get_source_concepts(self, source_id: int) -> list[dict[str, Any]]:
        assert source_id == 2
        return [
            {"id": "Country", "value": "Country"},
            {"id": "Series", "value": "Series"},
            {"id": "Time", "value": "Time"},
        ]

    def get_source_variables(self, source_id: int, concept_id: str) -> list[dict[str, Any]]:
        assert source_id == 2
        if concept_id.lower() == "country":
            return _fixture_records("countries_page_1.json")
        if concept_id.lower() == "series":
            return _fixture_records("indicators_page_1.json")
        raise AssertionError(f"unexpected concept: {concept_id}")

    def get_advanced_data(
        self,
        *,
        source_id: int,
        dimensions: dict[str, list[str] | str],
    ) -> list[dict[str, Any]]:
        raise AssertionError(f"source {source_id} should use classic API: {dimensions}")

    def get_observations(
        self,
        *,
        indicator_codes: list[str],
        country_codes: list[str],
        start_year: int,
        end_year: int,
        source_id: int | None = None,
    ) -> list[dict[str, Any]]:
        assert country_codes == ["DEU", "NLD"]
        assert len(indicator_codes) == 1
        assert start_year == 2024
        assert end_year == 2024
        assert source_id == 2

        indicator_code = indicator_codes[0]
        self.observation_calls.append((source_id, indicator_code))
        return [
            record
            for record in _fixture_records("observations_page_1.json")
            if record["indicator"]["id"] == indicator_code
        ]


def _config(tmp_path: Path):  # type: ignore[no-untyped-def]
    settings = AppSettings(
        _env_file=None,
        research_config_path=ROOT / "configs/research.yaml",
        indicators_config_path=ROOT / "configs/indicators.yaml",
        country_groups_config_path=ROOT / "configs/country_groups.yaml",
        raw_data_dir=tmp_path / "raw",
        processed_data_dir=tmp_path / "processed",
    )
    return load_application_config(settings)


def _csv_parquet_writer(frame: pd.DataFrame, path: Path) -> None:
    frame.to_csv(path, index=False)


def test_run_ingestion_creates_raw_quality_and_processed_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr("wb_insight.pipeline._write_parquet", _csv_parquet_writer)

    fake_client = FakeWorldBankClient()
    result = run_ingestion(
        config,
        country_codes=["DEU", "NLD"],
        indicator_codes=["gdp_per_capita", "population"],
        start_year=2024,
        end_year=2024,
        client=fake_client,
        run_id="test-run",
        started_at=datetime(2026, 8, 15, 10, 30, tzinfo=UTC),
    )

    assert result.run_id == "test-run"
    assert len(result.raw_artifacts) == 4
    assert all(artifact.path.exists() for artifact in result.raw_artifacts)
    assert result.quality_report.failed_count == 0
    assert result.countries_count == 2
    assert result.indicators_count == 2
    assert result.observations_count == 4
    assert fake_client.observation_calls == [
        (2, "NY.GDP.PCAP.CD"),
        (2, "SP.POP.TOTL"),
    ]

    observations = pd.read_csv(result.observations_path)
    assert set(observations["country_code"]) == {"DEU", "NLD"}
    assert set(observations["source_id"]) == {2}
    assert set(observations["dimensions_json"]) == {"{}"}

    report = json.loads(result.quality_report_path.read_text(encoding="utf-8"))
    assert report["run_id"] == "test-run"
    assert report["summary"]["failed"] == 0


def test_write_parquet_roundtrip_when_pyarrow_is_available(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    path = tmp_path / "sample.parquet"
    frame = pd.DataFrame({"country_code": ["DEU", "NLD"], "value": [1.0, 2.0]})

    _write_parquet(frame, path)

    loaded = pd.read_parquet(path)
    pd.testing.assert_frame_equal(loaded, frame)


def test_run_ingestion_rejects_indicator_missing_from_selected_source(tmp_path: Path) -> None:
    config = _config(tmp_path)
    fake_client = FakeWorldBankClient()

    with pytest.raises(ValueError, match="source/indicator pairs"):
        run_ingestion(
            config,
            country_codes=["DEU", "NLD"],
            indicator_codes=["urban_population_share"],
            start_year=2024,
            end_year=2024,
            client=fake_client,
            run_id="missing-indicator-test",
            started_at=datetime(2026, 8, 15, 10, 30, tzinfo=UTC),
        )

    assert fake_client.observation_calls == []


class ArbitraryIndicatorClient(FakeWorldBankClient):
    def get_indicators(self, source_id: int | None = None) -> list[dict[str, Any]]:
        assert source_id == 2
        records = super().get_indicators(source_id=source_id)
        return [
            *records,
            {
                "id": "SL.UEM.TOTL.ZS",
                "name": "Unemployment, total (% of total labor force)",
                "unit": "",
                "source": {"id": "2", "value": "World Development Indicators"},
                "topics": [{"id": "10", "value": "Social Protection & Labor"}],
            },
        ]

    def get_observations(
        self,
        *,
        indicator_codes: list[str],
        country_codes: list[str],
        start_year: int,
        end_year: int,
        source_id: int | None = None,
    ) -> list[dict[str, Any]]:
        if indicator_codes == ["SL.UEM.TOTL.ZS"]:
            assert source_id == 2
            self.observation_calls.append((source_id, "SL.UEM.TOTL.ZS"))
            return [
                {
                    "indicator": {
                        "id": "SL.UEM.TOTL.ZS",
                        "value": "Unemployment, total (% of total labor force)",
                    },
                    "country": {"id": "DE", "value": "Germany"},
                    "countryiso3code": "DEU",
                    "date": "2024",
                    "value": 3.4,
                    "unit": "",
                    "obs_status": "",
                    "decimal": 1,
                },
                {
                    "indicator": {
                        "id": "SL.UEM.TOTL.ZS",
                        "value": "Unemployment, total (% of total labor force)",
                    },
                    "country": {"id": "NL", "value": "Netherlands"},
                    "countryiso3code": "NLD",
                    "date": "2024",
                    "value": 3.7,
                    "unit": "",
                    "obs_status": "",
                    "decimal": 1,
                },
            ]
        return super().get_observations(
            indicator_codes=indicator_codes,
            country_codes=country_codes,
            start_year=start_year,
            end_year=end_year,
            source_id=source_id,
        )


def test_unregistered_unqualified_indicator_uses_default_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr("wb_insight.pipeline._write_parquet", _csv_parquet_writer)
    client = ArbitraryIndicatorClient()

    result = run_ingestion(
        config,
        country_codes=["DEU", "NLD"],
        indicator_codes=["SL.UEM.TOTL.ZS"],
        start_year=2024,
        end_year=2024,
        client=client,
        run_id="arbitrary-indicator-test",
        started_at=datetime(2026, 8, 15, 10, 30, tzinfo=UTC),
    )

    assert result.observations_count == 2
    assert result.quality_report.failed_count == 0
    assert client.observation_calls == [(2, "SL.UEM.TOTL.ZS")]


class MultiSourceClient(FakeWorldBankClient):
    def __init__(self) -> None:
        super().__init__()
        self.advanced_calls: list[tuple[int, dict[str, list[str] | str]]] = []

    def get_indicators(self, source_id: int | None = None) -> list[dict[str, Any]]:
        if source_id == 2:
            return super().get_indicators(source_id=source_id)
        assert source_id == 6
        return [
            {
                "id": "DT.DOD.DECT.CD",
                "name": "External debt stocks, total",
                "unit": "",
                "source": {"id": "6", "value": "International Debt Statistics"},
                "topics": [{"id": "20", "value": "External Debt"}],
            }
        ]

    def get_source_concepts(self, source_id: int) -> list[dict[str, Any]]:
        if source_id == 2:
            return super().get_source_concepts(source_id)
        assert source_id == 6
        return [
            {"id": "Country", "value": "Country"},
            {"id": "Series", "value": "Series"},
            {"id": "Counterpart-Area", "value": "Counterpart Area"},
            {"id": "Time", "value": "Time"},
        ]

    def get_source_variables(self, source_id: int, concept_id: str) -> list[dict[str, Any]]:
        if source_id == 2:
            return super().get_source_variables(source_id, concept_id)
        assert source_id == 6
        key = concept_id.lower()
        source = {"id": "6", "value": "International Debt Statistics"}
        if key == "country":
            return [
                {"id": "DEU", "value": "Germany", "source": source},
                {"id": "NLD", "value": "Netherlands", "source": source},
            ]
        if key == "series":
            return [
                {
                    "id": "DT.DOD.DECT.CD",
                    "value": "External debt stocks, total",
                    "source": source,
                }
            ]
        if key == "counterpart-area":
            return [
                {"id": "WLD", "value": "World", "source": source},
                {"id": "001", "value": "Austria", "source": source},
            ]
        raise AssertionError(f"unexpected source 6 concept: {concept_id}")

    def get_advanced_data(
        self,
        *,
        source_id: int,
        dimensions: dict[str, list[str] | str],
    ) -> list[dict[str, Any]]:
        assert source_id == 6
        self.advanced_calls.append((source_id, dimensions))
        counterpart = dimensions["Counterpart-Area"]
        counterpart_id = counterpart[0] if isinstance(counterpart, list) else counterpart
        counterpart_name = "World" if counterpart_id == "WLD" else "Austria"
        return [
            {
                "variable": [
                    {"concept": "Country", "id": country, "value": name},
                    {
                        "concept": "Series",
                        "id": "DT.DOD.DECT.CD",
                        "value": "External debt stocks, total",
                    },
                    {
                        "concept": "Counterpart-Area",
                        "id": counterpart_id,
                        "value": counterpart_name,
                    },
                    {"concept": "Time", "id": "YR2024", "value": "2024"},
                ],
                "value": value,
            }
            for country, name, value in (
                ("DEU", "Germany", 100.0),
                ("NLD", "Netherlands", 200.0),
            )
        ]


def test_run_ingestion_supports_classic_and_advanced_sources_in_one_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr("wb_insight.pipeline._write_parquet", _csv_parquet_writer)
    client = MultiSourceClient()

    result = run_ingestion(
        config,
        country_codes=["DEU", "NLD"],
        indicator_codes=["gdp_per_capita", "6:DT.DOD.DECT.CD"],
        start_year=2024,
        end_year=2024,
        client=client,
        run_id="multi-source-test",
        started_at=datetime(2026, 8, 15, 10, 30, tzinfo=UTC),
    )

    assert result.observations_count == 4
    assert result.quality_report.failed_count == 0
    assert client.observation_calls == [(2, "NY.GDP.PCAP.CD")]
    assert len(client.advanced_calls) == 1
    _, dimensions = client.advanced_calls[0]
    assert dimensions["Counterpart-Area"] == ["WLD"]
    assert {artifact.dataset for artifact in result.raw_artifacts} == {
        "countries",
        "source_2_concepts",
        "indicators_source_2",
        "source_6_concepts",
        "indicators_source_6",
        "observations_source_2",
        "observations_source_6",
    }

    observations = pd.read_csv(result.observations_path)
    keys = set(zip(observations["source_id"], observations["indicator_code"], strict=True))
    assert keys == {(2, "NY.GDP.PCAP.CD"), (6, "DT.DOD.DECT.CD")}
    debt = observations.loc[observations["source_id"] == 6]
    assert debt["dimensions_json"].str.contains('"Counterpart-Area"').all()
    assert debt["dimensions_json"].str.contains('"WLD"').all()


def test_explicit_extra_dimension_filter_overrides_ids_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr("wb_insight.pipeline._write_parquet", _csv_parquet_writer)
    client = MultiSourceClient()

    run_ingestion(
        config,
        country_codes=["DEU", "NLD"],
        indicator_codes=["6:DT.DOD.DECT.CD"],
        dimension_filters={6: {"Counterpart-Area": ["001"]}},
        start_year=2024,
        end_year=2024,
        client=client,
        run_id="dimension-override-test",
        started_at=datetime(2026, 8, 15, 10, 30, tzinfo=UTC),
    )

    assert client.advanced_calls[0][1]["Counterpart-Area"] == ["001"]


def test_unqualified_nondefault_indicator_does_not_scan_all_sources(tmp_path: Path) -> None:
    config = _config(tmp_path)
    client = MultiSourceClient()

    with pytest.raises(ValueError, match=r"2:DT\.DOD\.DECT\.CD"):
        run_ingestion(
            config,
            country_codes=["DEU", "NLD"],
            indicator_codes=["DT.DOD.DECT.CD"],
            start_year=2024,
            end_year=2024,
            client=client,
            run_id="default-source-test",
            started_at=datetime(2026, 8, 15, 10, 30, tzinfo=UTC),
        )

    assert client.advanced_calls == []
