"""Command-line entry points for local development and data ingestion."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from wb_insight import __version__
from wb_insight.config import ApplicationConfig, ConfigurationError, load_application_config
from wb_insight.ingestion import WorldBankAPIError
from wb_insight.logging import configure_logging
from wb_insight.pipeline import IngestionResult, run_ingestion
from wb_insight.quality import QualityCheckError

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
        f"{config.research.project.start_year}-{config.research.project.end_year}",
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
                "source_id": config.indicators.effective_source_id(indicator),
                "code": indicator.code,
                "alias": indicator.alias,
                "role": indicator.role,
                "category": indicator.category,
                "unit": indicator.unit,
                "display_unit": indicator.display_unit,
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


@app.command()
def ingest(
    countries: str | None = typer.Option(
        None,
        "--countries",
        help="Comma-separated ISO3 codes. Defaults to configs/research.yaml.",
    ),
    indicators: str | None = typer.Option(
        None,
        "--indicators",
        help=(
            "Comma-separated registry aliases, raw indicator codes, or explicit "
            "SOURCE_ID:INDICATOR_CODE selectors. Unregistered unqualified codes use "
            "the registry default source (normally WDI/source 2)."
        ),
    ),
    dimensions: str | None = typer.Option(
        None,
        "--dimensions",
        help=(
            "Optional comma-separated filters for extra source dimensions, for example "
            "6:Counterpart-Area=WLD or 57:Version=199704. Multiple values use ';'. "
            "IDS Counterpart-Area defaults to WLD when omitted."
        ),
    ),
    start_year: int | None = typer.Option(
        None,
        "--start-year",
        min=1960,
        max=2100,
        help="First observation year. Defaults to the research configuration.",
    ),
    end_year: int | None = typer.Option(
        None,
        "--end-year",
        min=1960,
        max=2100,
        help="Last observation year. Defaults to the research configuration.",
    ),
) -> None:
    """Run the local World Bank API -> raw JSON -> Parquet pipeline."""

    config = _load_or_exit()
    configure_logging(config.settings.log_level)

    try:
        result = run_ingestion(
            config,
            country_codes=_parse_csv_option(countries),
            indicator_codes=_parse_csv_option(indicators),
            dimension_filters=_parse_dimension_filters(dimensions),
            start_year=start_year,
            end_year=end_year,
        )
    except QualityCheckError as exc:
        error_console.print(f"[bold red]Data quality failure:[/bold red] {exc}")
        error_console.print_json(json.dumps(exc.report.to_dict(), ensure_ascii=False))
        raise typer.Exit(code=2) from exc
    except (WorldBankAPIError, OSError, ValueError) as exc:
        error_console.print(f"[bold red]Ingestion failed:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    _print_ingestion_result(result)


def _parse_csv_option(value: str | None) -> list[str] | None:
    if value is None:
        return None
    values = [part.strip() for part in value.split(",") if part.strip()]
    if not values:
        raise ValueError("comma-separated option cannot be empty")
    return values


def _parse_dimension_filters(
    value: str | None,
) -> dict[int, dict[str, tuple[str, ...]]] | None:
    if value is None:
        return None

    result: dict[int, dict[str, tuple[str, ...]]] = {}
    for raw_spec in value.split(","):
        spec = raw_spec.strip()
        if not spec:
            continue
        if "=" not in spec or ":" not in spec.split("=", 1)[0]:
            raise ValueError("dimension filters must use SOURCE_ID:CONCEPT=VALUE1;VALUE2")
        left, raw_values = spec.split("=", 1)
        source_text, concept = left.split(":", 1)
        if not source_text.isdigit() or int(source_text) <= 0 or not concept.strip():
            raise ValueError("dimension filters must use SOURCE_ID:CONCEPT=VALUE1;VALUE2")
        values = tuple(part.strip() for part in raw_values.split(";") if part.strip())
        if not values:
            raise ValueError(f"dimension filter {spec!r} has no values")
        source_id = int(source_text)
        source_filters = result.setdefault(source_id, {})
        if concept.strip() in source_filters:
            raise ValueError(f"duplicate dimension filter: {source_id}:{concept.strip()}")
        source_filters[concept.strip()] = values

    if not result:
        raise ValueError("dimension filters cannot be empty")
    return result


def _print_ingestion_result(result: IngestionResult) -> None:
    table = Table(title=f"Ingestion run {result.run_id}")
    table.add_column("Artifact")
    table.add_column("Rows", justify="right")
    table.add_column("Path")
    table.add_row("Countries", str(result.countries_count), str(result.countries_path))
    table.add_row("Indicators", str(result.indicators_count), str(result.indicators_path))
    table.add_row("Observations", str(result.observations_count), str(result.observations_path))
    table.add_row("Quality report", "-", str(result.quality_report_path))
    console.print(table)
    console.print(
        "Quality checks: "
        f"[green]{result.quality_report.passed_count} passed[/green], "
        f"[yellow]{result.quality_report.warning_count} warnings[/yellow], "
        f"[red]{result.quality_report.failed_count} failed[/red]"
    )
    console.print("[bold green]Ingestion completed successfully.[/bold green]")
