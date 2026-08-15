from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wb_insight import __version__
from wb_insight.cli import app
from wb_insight.pipeline import IngestionResult
from wb_insight.quality import CheckResult, QualityReport

runner = CliRunner()


def test_doctor_succeeds_for_repository_configuration() -> None:
    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert f"WB Insight Agent {__version__}" in result.output
    assert "Configuration is valid" in result.output


def test_show_config_prints_target_and_country() -> None:
    result = runner.invoke(app, ["show-config"])

    assert result.exit_code == 0, result.output
    assert "gdp_per_capita" in result.output
    assert "LVA" in result.output
    assert "NY.GDP.PCAP.CD" in result.output


def test_ingest_command_passes_cli_overrides_to_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    now = datetime(2026, 8, 15, 11, 0, tzinfo=UTC)
    report = QualityReport(
        (
            CheckResult(
                name="test",
                status="passed",
                message="ok",
            ),
        )
    )

    def fake_run_ingestion(config, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return IngestionResult(
            run_id="cli-test",
            started_at=now,
            finished_at=now,
            raw_artifacts=(),
            countries_path=tmp_path / "countries.parquet",
            indicators_path=tmp_path / "indicators.parquet",
            observations_path=tmp_path / "observations.parquet",
            quality_report_path=tmp_path / "quality_report.json",
            countries_count=2,
            indicators_count=2,
            observations_count=4,
            quality_report=report,
        )

    monkeypatch.setattr("wb_insight.cli.run_ingestion", fake_run_ingestion)

    result = runner.invoke(
        app,
        [
            "ingest",
            "--countries",
            "DEU,NLD",
            "--indicators",
            "gdp_per_capita,6:DT.DOD.DECT.CD",
            "--dimensions",
            "6:Counterpart-Area=WLD",
            "--start-year",
            "2024",
            "--end-year",
            "2024",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["country_codes"] == ["DEU", "NLD"]
    assert captured["indicator_codes"] == ["gdp_per_capita", "6:DT.DOD.DECT.CD"]
    assert captured["dimension_filters"] == {6: {"Counterpart-Area": ("WLD",)}}
    assert captured["start_year"] == 2024
    assert captured["end_year"] == 2024
    assert "Ingestion completed successfully" in result.output
