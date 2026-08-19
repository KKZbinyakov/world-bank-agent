"""FastAPI application exposing versioned read-only analytical tools."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from wb_insight import __version__
from wb_insight.api.exception_handlers import register_exception_handlers
from wb_insight.api.middleware import RequestContextMiddleware
from wb_insight.api.routers import health, metadata, tools
from wb_insight.config import AppSettings, get_settings
from wb_insight.marts import load_mart_config


def create_app(settings: AppSettings | None = None) -> FastAPI:
    """Create an application with frozen settings and presentation labels."""

    app_settings = settings or get_settings()
    docs_url = "/docs" if app_settings.api_docs_enabled else None
    redoc_url = "/redoc" if app_settings.api_docs_enabled else None
    app = FastAPI(
        title="WB Insight Tool API",
        summary="Versioned read-only analytical tools over World Bank data.",
        version=__version__,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url="/openapi.json",
    )
    app.state.settings = app_settings
    app.state.country_labels = _load_country_labels(app_settings.marts_config_path)
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(metadata.router)
    app.include_router(tools.router)
    return app


def run() -> None:
    """Run the local API server; Yandex deployment may override `PORT`."""

    settings = get_settings()
    port = int(os.getenv("PORT", str(settings.api_port)))
    uvicorn.run(
        "wb_insight.api.main:app",
        host=settings.api_host,
        port=port,
        log_level=settings.log_level.lower(),
    )


def _load_country_labels(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    return load_mart_config(path).country_labels


app = create_app()


if __name__ == "__main__":  # pragma: no cover
    run()
