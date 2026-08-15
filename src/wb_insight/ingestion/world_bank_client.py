"""HTTP client for the World Bank Indicators and Advanced Data APIs v2."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import httpx
from pydantic import ValidationError

from wb_insight.ingestion.schemas import (
    WorldBankAPIError,
    WorldBankPageMetadata,
    WorldBankResponseError,
)

JsonRecord = dict[str, Any]
AdvancedExtractor = Callable[[Mapping[str, Any]], list[JsonRecord]]

_RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


class WorldBankClient:
    """Small, testable client for the public World Bank API v2.

    The classic Indicators API is used for standard three-dimensional sources such
    as WDI. The Advanced Data API methods expose arbitrary source concepts and are
    used for multidimensional databases such as International Debt Statistics.
    All methods deliberately return raw JSON records; normalization belongs to the
    transformation layer.
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
        """Return classic indicator metadata, optionally restricted to one source."""

        params: dict[str, str | int] = {}
        if source_id is not None:
            self._validate_source_id(source_id)
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
        """Return classic indicator observations for countries and years."""

        indicators = self._normalize_indicator_codes(indicator_codes)
        countries = self._normalize_country_codes(country_codes)
        self._validate_year_range(start_year, end_year)
        if len(indicators) > 60:
            raise ValueError("World Bank API accepts at most 60 indicators per request")
        if len(indicators) > 1 and source_id is None:
            raise ValueError("source_id is required when requesting multiple indicators")
        if source_id is not None:
            self._validate_source_id(source_id)

        country_segment = ";".join(countries)
        indicator_segment = ";".join(indicators)
        params: dict[str, str | int] = {"date": f"{start_year}:{end_year}"}
        if source_id is not None:
            params["source"] = source_id

        return self._get_paginated(
            f"/country/{country_segment}/indicator/{indicator_segment}",
            params=params,
        )

    def get_source_concepts(self, source_id: int) -> list[JsonRecord]:
        """Return data dimensions (concepts) exposed by one World Bank source."""

        self._validate_source_id(source_id)
        endpoints = (
            f"/sources/{source_id}/concepts/data",
            f"/sources/{source_id}/concepts",
        )

        def extract(payload: Mapping[str, Any]) -> list[JsonRecord]:
            records: list[JsonRecord] = []
            for source in self._advanced_source_objects(payload, source_id):
                raw_concepts = source.get("concept")
                if not isinstance(raw_concepts, list):
                    continue
                source_name = str(source.get("name", "")).strip()
                for concept in raw_concepts:
                    if not isinstance(concept, Mapping):
                        continue
                    record = dict(concept)
                    record["source"] = {"id": str(source_id), "value": source_name}
                    records.append(record)
            return records

        return self._get_advanced_with_fallback(endpoints, extractor=extract)

    def get_source_variables(self, source_id: int, concept_id: str) -> list[JsonRecord]:
        """Return all variables of one source concept, for example Series or Country."""

        self._validate_source_id(source_id)
        concept = self._normalize_concept_id(concept_id)
        concept_path = self._concept_path(concept)
        endpoints = (
            f"/sources/{source_id}/{concept_path}/data",
            f"/sources/{source_id}/{concept_path}",
        )

        def extract(payload: Mapping[str, Any]) -> list[JsonRecord]:
            records: list[JsonRecord] = []
            for source in self._advanced_source_objects(payload, source_id):
                raw_concepts = source.get("concept")
                if not isinstance(raw_concepts, list):
                    continue
                source_name = str(source.get("name", "")).strip()
                for raw_concept in raw_concepts:
                    if not isinstance(raw_concept, Mapping):
                        continue
                    returned_id = str(raw_concept.get("id", "")).strip()
                    if self._concept_key(returned_id) != self._concept_key(concept):
                        continue
                    raw_variables = raw_concept.get("variable")
                    if not isinstance(raw_variables, list):
                        continue
                    for variable in raw_variables:
                        if not isinstance(variable, Mapping):
                            continue
                        record = dict(variable)
                        record["concept_id"] = returned_id or concept
                        record["source"] = {"id": str(source_id), "value": source_name}
                        records.append(record)
            return records

        return self._get_advanced_with_fallback(endpoints, extractor=extract)

    def get_advanced_data(
        self,
        *,
        source_id: int,
        dimensions: Mapping[str, Sequence[str] | str],
    ) -> list[JsonRecord]:
        """Retrieve arbitrary multidimensional source data through the Advanced API.

        ``dimensions`` is ordered and maps source concept names to one or more
        variable ids. For example IDS can be queried with Country, Series,
        Counterpart-Area and Time. The special value ``"all"`` is accepted.
        """

        self._validate_source_id(source_id)
        if not dimensions:
            raise ValueError("at least one source dimension must be selected")

        path_parts = [f"/sources/{source_id}"]
        for concept_id, raw_values in dimensions.items():
            concept = self._normalize_concept_id(concept_id)
            values = self._normalize_dimension_values(raw_values)
            path_parts.extend((self._concept_path(concept), ";".join(values)))
        endpoint = "/".join(part.strip("/") for part in path_parts if part)
        endpoint = "/" + endpoint
        endpoints = (endpoint + "/data", endpoint)

        def extract(payload: Mapping[str, Any]) -> list[JsonRecord]:
            records: list[JsonRecord] = []
            for source in self._advanced_source_objects(payload, source_id):
                raw_data = source.get("data")
                if not isinstance(raw_data, list):
                    continue
                for item in raw_data:
                    if isinstance(item, Mapping):
                        records.append(dict(item))
            return records

        return self._get_advanced_with_fallback(endpoints, extractor=extract)

    def _get_paginated(
        self,
        endpoint: str,
        *,
        params: Mapping[str, str | int] | None = None,
    ) -> list[JsonRecord]:
        """Fetch all pages from a classic World Bank JSON envelope."""

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
            expected_pages = self._validate_pagination(
                metadata,
                requested_page=page,
                expected_pages=expected_pages,
            )
            records.extend(page_records)
            page += 1

        return records

    def _get_advanced_with_fallback(
        self,
        endpoints: Sequence[str],
        *,
        extractor: AdvancedExtractor,
    ) -> list[JsonRecord]:
        last_error: WorldBankAPIError | None = None
        for endpoint in endpoints:
            try:
                return self._get_advanced_paginated(endpoint, extractor=extractor)
            except WorldBankAPIError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise WorldBankAPIError("no Advanced API endpoints were provided")

    def _get_advanced_paginated(
        self,
        endpoint: str,
        *,
        extractor: AdvancedExtractor,
    ) -> list[JsonRecord]:
        """Fetch all pages from the object-shaped Advanced Data API."""

        records: list[JsonRecord] = []
        page = 1
        expected_pages: int | None = None

        while expected_pages is None or page <= expected_pages:
            params: dict[str, str | int] = {
                "format": "json",
                "per_page": self._per_page,
                "page": page,
            }
            payload = self._request_json(endpoint, params)
            if not isinstance(payload, Mapping):
                raise WorldBankResponseError(
                    f"expected World Bank Advanced API JSON object, got {type(payload).__name__}"
                )
            if "message" in payload and "source" not in payload:
                raise WorldBankAPIError(self._extract_api_error(payload))
            try:
                metadata = WorldBankPageMetadata.model_validate(payload)
            except ValidationError as exc:
                raise WorldBankResponseError("invalid Advanced API pagination metadata") from exc

            expected_pages = self._validate_pagination(
                metadata,
                requested_page=page,
                expected_pages=expected_pages,
            )
            records.extend(extractor(payload))
            page += 1

        return records

    @staticmethod
    def _validate_pagination(
        metadata: WorldBankPageMetadata,
        *,
        requested_page: int,
        expected_pages: int | None,
    ) -> int:
        if metadata.page != requested_page:
            raise WorldBankResponseError(
                f"expected page {requested_page}, API returned page {metadata.page}"
            )
        if expected_pages is None:
            return metadata.pages
        if metadata.pages != expected_pages:
            raise WorldBankResponseError("pagination metadata changed while reading the result set")
        return expected_pages

    @staticmethod
    def _advanced_source_objects(
        payload: Mapping[str, Any],
        source_id: int,
    ) -> list[Mapping[str, Any]]:
        raw_source = payload.get("source")
        if isinstance(raw_source, Mapping):
            candidates = [raw_source]
        elif isinstance(raw_source, list):
            candidates = [item for item in raw_source if isinstance(item, Mapping)]
        else:
            raise WorldBankResponseError("Advanced API response is missing a source object")

        matched = [
            source for source in candidates if str(source.get("id", "")).strip() == str(source_id)
        ]
        if not matched:
            raise WorldBankResponseError(
                f"Advanced API response does not contain requested source {source_id}"
            )
        return matched

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

        raise WorldBankAPIError(f"World Bank request failed: {last_error}")  # pragma: no cover

    def _sleep_before_retry(self, attempt: int) -> None:
        if self._retry_wait_seconds == 0:
            return
        time.sleep(self._retry_wait_seconds * (2 ** (attempt - 1)))

    @staticmethod
    def _parse_page(payload: object) -> tuple[WorldBankPageMetadata, list[JsonRecord]]:
        if isinstance(payload, Mapping):
            raise WorldBankAPIError(WorldBankClient._extract_api_error(payload))

        if not isinstance(payload, list):
            raise WorldBankResponseError(
                "expected World Bank JSON envelope [metadata, records], "
                f"got {type(payload).__name__}"
            )
        if len(payload) != 2:
            preview = repr(payload)[:300]
            raise WorldBankResponseError(
                "expected World Bank JSON envelope [metadata, records], "
                f"got list with {len(payload)} elements: {preview}"
            )

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

    @staticmethod
    def _validate_year_range(start_year: int, end_year: int) -> None:
        if start_year < 1960 or end_year > 2100:
            raise ValueError("year range must stay between 1960 and 2100")
        if end_year < start_year:
            raise ValueError("end_year must be greater than or equal to start_year")

    @staticmethod
    def _validate_source_id(source_id: int) -> None:
        if source_id <= 0:
            raise ValueError("source_id must be greater than zero")

    @staticmethod
    def _normalize_concept_id(value: str) -> str:
        concept = value.strip()
        if not concept or "/" in concept or ";" in concept:
            raise ValueError(f"invalid source concept id: {value!r}")
        return concept

    @staticmethod
    def _concept_path(concept_id: str) -> str:
        path = re.sub(r"[^a-z0-9]+", "-", concept_id.lower()).strip("-")
        if not path:
            raise ValueError(f"invalid source concept id: {concept_id!r}")
        return path

    @classmethod
    def _concept_key(cls, concept_id: str) -> str:
        return cls._concept_path(concept_id)

    @staticmethod
    def _normalize_dimension_values(values: Sequence[str] | str) -> list[str]:
        raw_values = [values] if isinstance(values, str) else list(values)
        if not raw_values:
            raise ValueError("source dimension values cannot be empty")
        normalized = [str(value).strip() for value in raw_values]
        invalid = [
            value
            for value in normalized
            if not value or "/" in value or ";" in value or " " in value
        ]
        if invalid:
            raise ValueError(f"invalid source dimension values: {', '.join(invalid)}")
        if len(normalized) != len(set(normalized)):
            raise ValueError("source dimension values must be unique")
        return normalized
