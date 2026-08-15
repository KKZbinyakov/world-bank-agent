"""Schemas used by the World Bank Indicators API client."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class WorldBankPageMetadata(BaseModel):
    """Pagination metadata returned as the first element of a JSON response."""

    model_config = ConfigDict(extra="allow")

    page: int = Field(ge=1)
    pages: int = Field(ge=0)
    per_page: int = Field(ge=1)
    total: int = Field(ge=0)


class WorldBankAPIError(RuntimeError):
    """Raised when the World Bank API rejects a request or stays unavailable."""


class WorldBankResponseError(WorldBankAPIError):
    """Raised when the API response does not match the documented JSON envelope."""
