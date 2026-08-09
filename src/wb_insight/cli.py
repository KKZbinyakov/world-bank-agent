"""Command-line entry points for project bootstrap and diagnostics."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from wb_insight import __version__
from wb_insight.config import ApplicationConfig, ConfigurationError, load_application_config
from wb_insight.logging import configure_logging

app = typer.Typer(
    name="wb-insight",
    help="Development commands for WB Insight Agent.",
    no_args_is_help=True,
)
console = Console()
error_console = Console(stderr=True)


def _load_or_exit() -> ApplicationConfig:
    """Load project configuration and turn validation failures into a clean CLI error."""

    try:
        return load_application_config()
    except ConfigurationError as exc:
        error_console.print(f"[bold red]Configuration error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command()
def doctor() -> None:
    """Validate local settings and all YAML configuration files."""

    config = _load_or_exit()
    configure_logging(config.settings.log_level)

    directory_statuses: list[tuple[str, Path, str]] = []
    for label, directory in (
        ("raw data", config.settings.raw_data_dir),
        ("processed data", config.settings.processed_data_dir),
    ):
        status = "ready" if directory.is_dir() else "missing"
        directory_statuses.append((label, directory, status))

    table = Table(title=f"WB Insight Agent {__version__}: environment check")
    table.add_column("Check")
    table.add_column("Value")
    table.add_column("Status")
    table.add_row("Environment", config.settings.app_env, "OK")
    table.add_row("World Bank API", str(config.settings.wb_api_base_url), "OK")
    table.add_row(
        "Research period",
        f"{config.research.project.start_year}–{config.research.project.end_year}",
        "OK",
    )
    table.add_row("Pilot countries", str(len(config.research.scope.countries)), "OK")
    table.add_row("Enabled indicators", str(len(config.indicators.enabled_indicators())), "OK")
    table.add_row("Custom groups", str(len(config.country_groups.groups)), "OK")
    for label, directory, status in directory_statuses:
        table.add_row(label, str(directory), "OK" if status == "ready" else "MISSING")

    console.print(table)
    missing_directories = [
        str(directory) for _, directory, status in directory_statuses if status != "ready"
    ]
    if missing_directories:
        error_console.print(
            "[yellow]Create the missing data directories before running ingestion:[/yellow] "
            + ", ".join(missing_directories)
        )
        raise typer.Exit(code=1)

    console.print("[bold green]Configuration is valid and the local project is ready.[/bold green]")


@app.command("show-config")
def show_config() -> None:
    """Print a safe summary of the currently selected research scope."""

    config = _load_or_exit()
    enabled_indicators = config.indicators.enabled_indicators()
    payload = {
        "project": {
            "name": config.research.project.name,
            "slug": config.research.project.slug,
            "period": [
                config.research.project.start_year,
                config.research.project.end_year,
            ],
        },
        "research": {
            "target_indicator": config.research.research.target_indicator,
            "questions": config.research.research.questions,
        },
        "scope": {
            "countries": config.research.scope.countries,
            "comparison_groups": config.research.scope.comparison_groups,
        },
        "indicators": [
            {
                "code": indicator.code,
                "alias": indicator.alias,
                "role": indicator.role,
                "category": indicator.category,
            }
            for indicator in enabled_indicators
        ],
        "runtime": {
            "environment": config.settings.app_env,
            "world_bank_api": str(config.settings.wb_api_base_url),
            "raw_data_dir": str(config.settings.raw_data_dir),
            "processed_data_dir": str(config.settings.processed_data_dir),
        },
    }
    console.print_json(json.dumps(payload, ensure_ascii=False))
