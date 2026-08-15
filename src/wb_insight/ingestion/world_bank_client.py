"""HTTP client for the World Bank Indicators API v2."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import Any

import httpx
from pydantic import ValidationError

from wb_insight.ingestion.schemas import (
    WorldBankAPIError,
    WorldBankPageMetadata,
    WorldBankResponseError,
)

JsonRecord = dict[str, Any]

_RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


class WorldBankClient:
    """Small, testable client for the public World Bank Indicators API.

    The client deliberately returns raw JSON records. Normalization into project data
    models belongs to the transformation layer and is implemented separately.
    """

    def __init__(
        self,
        *,
        base_url: str = "https://api.worldbank.org/v2",
        timeout_seconds: float = 30.0,
        per_page: int = 1000,
        max_attempts: int = 3,
        retry_wait_seconds: float = 0.5,
        http_client: httpx.Client | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if per_page <= 0:
            raise ValueError("per_page must be greater than zero")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be greater than zero")
        if retry_wait_seconds < 0:
            raise ValueError("retry_wait_seconds cannot be negative")

        self._per_page = per_page
        self._max_attempts = max_attempts
        self._retry_wait_seconds = retry_wait_seconds
        self._owns_http_client = http_client is None
        self._http = http_client or httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={"Accept": "application/json"},
        )

    def __enter__(self) -> WorldBankClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the internally created HTTP client."""

        if self._owns_http_client:
            self._http.close()

    def get_countries(self) -> list[JsonRecord]:
        """Return every country/aggregate record exposed by the country endpoint."""

        return self._get_paginated("/country")

    def get_indicators(self, source_id: int | None = None) -> list[JsonRecord]:
        """Return indicator metadata, optionally restricted to one World Bank source."""

        params: dict[str, str | int] = {}
        if source_id is not None:
            if source_id <= 0:
                raise ValueError("source_id must be greater than zero")
            params["source"] = source_id
        return self._get_paginated("/indicator", params=params)

    def get_observations(
        self,
        *,
        indicator_codes: Sequence[str],
        country_codes: Sequence[str],
        start_year: int,
        end_year: int,
        source_id: int | None = None,
    ) -> list[JsonRecord]:
        """Return raw observations for selected countries, indicators and years.

        World Bank requires a source id when multiple indicators are requested in one
        call. For a single indicator it remains optional.
        """

        indicators = self._normalize_indicator_codes(indicator_codes)
        countries = self._normalize_country_codes(country_codes)
        if start_year < 1960 or end_year > 2100:
            raise ValueError("year range must stay between 1960 and 2100")
        if end_year < start_year:
            raise ValueError("end_year must be greater than or equal to start_year")
        if len(indicators) > 60:
            raise ValueError("World Bank API accepts at most 60 indicators per request")
        if len(indicators) > 1 and source_id is None:
            raise ValueError("source_id is required when requesting multiple indicators")
        if source_id is not None and source_id <= 0:
            raise ValueError("source_id must be greater than zero")

        country_segment = ";".join(countries)
        indicator_segment = ";".join(indicators)
        params: dict[str, str | int] = {"date": f"{start_year}:{end_year}"}
        if source_id is not None:
            params["source"] = source_id

        return self._get_paginated(
            f"/country/{country_segment}/indicator/{indicator_segment}",
            params=params,
        )

    def _get_paginated(
        self,
        endpoint: str,
        *,
        params: Mapping[str, str | int] | None = None,
    ) -> list[JsonRecord]:
        """Fetch all pages from a World Bank JSON endpoint."""

        records: list[JsonRecord] = []
        page = 1
        expected_pages: int | None = None

        while expected_pages is None or page <= expected_pages:
            request_params: dict[str, str | int] = {
                "format": "json",
                "per_page": self._per_page,
                "page": page,
            }
            if params:
                request_params.update(params)

            payload = self._request_json(endpoint, request_params)
            metadata, page_records = self._parse_page(payload)

            if metadata.page != page:
                raise WorldBankResponseError(
                    f"expected page {page}, API returned page {metadata.page}"
                )
            if expected_pages is None:
                expected_pages = metadata.pages
            elif metadata.pages != expected_pages:
                raise WorldBankResponseError(
                    "pagination metadata changed while reading the result set"
                )

            records.extend(page_records)
            page += 1

        return records

    def _request_json(
        self,
        endpoint: str,
        params: Mapping[str, str | int],
    ) -> object:
        last_error: Exception | None = None

        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._http.get(endpoint, params=params)
            except httpx.RequestError as exc:
                last_error = exc
                if attempt < self._max_attempts:
                    self._sleep_before_retry(attempt)
                    continue
                raise WorldBankAPIError(
                    f"World Bank request failed after {attempt} attempts: {exc}"
                ) from exc

            if response.status_code in _RETRYABLE_STATUS_CODES:
                last_error = WorldBankAPIError(
                    f"World Bank API returned retryable HTTP {response.status_code}"
                )
                if attempt < self._max_attempts:
                    self._sleep_before_retry(attempt)
                    continue
                raise last_error

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                message = self._response_error_message(response)
                raise WorldBankAPIError(
                    f"World Bank API returned HTTP {response.status_code}: {message}"
                ) from exc

            try:
                return response.json()
            except ValueError as exc:
                raise WorldBankResponseError("World Bank API returned invalid JSON") from exc

        # The loop always either returns or raises. This keeps the branch explicit for mypy.
        raise WorldBankAPIError(f"World Bank request failed: {last_error}")  # pragma: no cover

    def _sleep_before_retry(self, attempt: int) -> None:
        if self._retry_wait_seconds == 0:
            return
        time.sleep(self._retry_wait_seconds * (2 ** (attempt - 1)))

    @staticmethod
    def _parse_page(payload: object) -> tuple[WorldBankPageMetadata, list[JsonRecord]]:
        if isinstance(payload, Mapping):
            raise WorldBankAPIError(WorldBankClient._extract_api_error(payload))

        if not isinstance(payload, list) or len(payload) != 2:
            raise WorldBankResponseError("expected World Bank JSON envelope [metadata, records]")

        metadata_raw, records_raw = payload
        if not isinstance(metadata_raw, Mapping):
            raise WorldBankResponseError("pagination metadata must be a JSON object")

        try:
            metadata = WorldBankPageMetadata.model_validate(metadata_raw)
        except ValidationError as exc:
            raise WorldBankResponseError("invalid pagination metadata") from exc

        if records_raw is None:
            return metadata, []
        if not isinstance(records_raw, list):
            raise WorldBankResponseError("records element must be a JSON array or null")
        if not all(isinstance(record, dict) for record in records_raw):
            raise WorldBankResponseError("every World Bank record must be a JSON object")

        return metadata, list(records_raw)

    @staticmethod
    def _extract_api_error(payload: Mapping[object, object]) -> str:
        raw_message = payload.get("message")
        if isinstance(raw_message, list):
            messages: list[str] = []
            for item in raw_message:
                if isinstance(item, Mapping):
                    value = item.get("value") or item.get("key") or item.get("message")
                    if value:
                        messages.append(str(value))
                elif item:
                    messages.append(str(item))
            if messages:
                return "; ".join(messages)
        if raw_message:
            return str(raw_message)
        return f"World Bank API returned an error object: {dict(payload)}"

    @staticmethod
    def _response_error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            text = response.text.strip()
            return text[:300] if text else "no response body"
        if isinstance(payload, Mapping):
            return WorldBankClient._extract_api_error(payload)
        return str(payload)[:300]

    @staticmethod
    def _normalize_indicator_codes(values: Sequence[str]) -> list[str]:
        if not values:
            raise ValueError("at least one indicator code is required")
        normalized = [value.strip().upper() for value in values]
        invalid = [
            value
            for value in normalized
            if not value or ";" in value or "/" in value or " " in value
        ]
        if invalid:
            raise ValueError(f"invalid indicator codes: {', '.join(invalid)}")
        if len(normalized) != len(set(normalized)):
            raise ValueError("indicator codes must be unique")
        return normalized

    @staticmethod
    def _normalize_country_codes(values: Sequence[str]) -> list[str]:
        if not values:
            raise ValueError("at least one country code is required")
        normalized = [value.strip().upper() for value in values]
        invalid = [value for value in normalized if len(value) != 3 or not value.isalpha()]
        if invalid:
            raise ValueError(f"invalid country codes: {', '.join(invalid)}")
        if len(normalized) != len(set(normalized)):
            raise ValueError("country codes must be unique")
        return normalized
