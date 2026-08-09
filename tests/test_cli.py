from __future__ import annotations

from typer.testing import CliRunner

from wb_insight import __version__
from wb_insight.cli import app

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
