"""Bounded read-only access to ClickHouse analytical marts."""

from __future__ import annotations

import importlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Protocol, cast

from wb_insight.analytics.models import (
    AmbiguousMetricError,
    CountryCatalogEntry,
    CountrySnapshotResult,
    CurrentRunSummary,
    DataQualityEntry,
    DataQualityResult,
    DimensionRequiredError,
    IndicatorCatalogEntry,
    MetricNotFoundError,
    MetricRequest,
    RepositoryReadiness,
    ResolvedMetric,
    ResultLimitError,
    SeriesCoverage,
    SnapshotMode,
    SnapshotPoint,
    TimeseriesPoint,
    TimeseriesResult,
)
from wb_insight.config import AppSettings

_COUNTRY_RE = re.compile(r"^[A-Z]{3}$")
_EXPLICIT_METRIC_RE = re.compile(r"^(?P<source>\d+):(?P<code>[A-Za-z0-9._-]+)$")
_REQUIRED_ANALYTICAL_OBJECTS = (
    "etl_run",
    "mart_country_snapshot",
    "mart_data_quality",
    "mart_indicator_timeseries",
    "mart_metric_catalog",
)


class QueryResult(Protocol):
    """Minimal query result used from clickhouse-connect."""

    @property
    def result_set(self) -> list[list[Any] | tuple[Any, ...]]: ...


class AnalyticalClient(Protocol):
    """Read-only client protocol intentionally exposing no command/insert methods."""

    def query(
        self,
        query: str,
        parameters: Mapping[str, Any] | Sequence[Any] | None = None,
    ) -> QueryResult: ...

    def close(self) -> None: ...


class AnalyticalRepository:
    """Safe analytical queries over the current ClickHouse marts.

    The public API accepts only validated countries, metrics and periods. Callers never
    provide SQL fragments or table names.
    """

    def __init__(
        self,
        client: AnalyticalClient,
        *,
        max_countries: int = 50,
        max_metrics: int = 20,
        max_years: int = 100,
        max_result_rows: int = 50_000,
    ) -> None:
        if min(max_countries, max_metrics, max_years, max_result_rows) < 1:
            raise ValueError("analytical repository limits must be positive")
        self._client = client
        self.max_countries = max_countries
        self.max_metrics = max_metrics
        self.max_years = max_years
        self.max_result_rows = max_result_rows

    @classmethod
    def from_settings(cls, settings: AppSettings) -> AnalyticalRepository:
        """Create an analytical client from application settings."""

        if not settings.clickhouse_host:
            raise ValueError("CLICKHOUSE_HOST is required")
        if not settings.clickhouse_user:
            raise ValueError("CLICKHOUSE_USER is required")
        if not settings.clickhouse_password:
            raise ValueError("CLICKHOUSE_PASSWORD is required")

        module = importlib.import_module("clickhouse_connect")
        get_client = cast(Any, module).get_client
        client = cast(
            AnalyticalClient,
            get_client(
                host=settings.clickhouse_host,
                port=settings.clickhouse_port,
                username=settings.clickhouse_user,
                password=settings.clickhouse_password,
                database=settings.clickhouse_database,
                secure=settings.clickhouse_secure,
            ),
        )
        return cls(client)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> AnalyticalRepository:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def ping(self) -> bool:
        """Return whether a minimal read-only query succeeds."""

        rows = self._query("SELECT 1")
        return bool(rows and rows[0] and int(rows[0][0]) == 1)

    def get_readiness(self) -> RepositoryReadiness:
        """Validate required analytical objects and an active loaded run."""

        rows = self._query(
            """
            SELECT name
            FROM system.tables
            WHERE database = currentDatabase()
              AND name IN {required_objects:Array(String)}
            """,
            {"required_objects": list(_REQUIRED_ANALYTICAL_OBJECTS)},
        )
        available = {str(row[0]) for row in rows if row}
        missing = tuple(name for name in _REQUIRED_ANALYTICAL_OBJECTS if name not in available)
        current_run = self.get_current_run() if not missing else None
        return RepositoryReadiness(
            ready=not missing and current_run is not None,
            current_run_id=current_run.run_id if current_run is not None else None,
            missing_objects=missing,
        )

    def get_current_run(self) -> CurrentRunSummary | None:
        """Return scope metadata for the most recently loaded analytical run."""

        rows = self._query(
            """
            WITH latest_run AS
            (
                SELECT run_id, loaded_at
                FROM etl_run
                WHERE status = 'loaded'
                ORDER BY loaded_at DESC, run_id DESC
                LIMIT 1
            )
            SELECT
                r.run_id,
                r.loaded_at,
                uniqExact(t.country_code) AS country_count,
                uniqExact(tuple(t.source_id, t.indicator_code)) AS indicator_count,
                count(t.year) AS observation_count,
                nullIf(min(t.year), 0) AS start_year,
                nullIf(max(t.year), 0) AS end_year,
                arraySort(groupUniqArray(t.source_id)) AS source_ids
            FROM latest_run AS r
            LEFT JOIN mart_indicator_timeseries AS t ON t.run_id = r.run_id
            GROUP BY r.run_id, r.loaded_at
            """
        )
        if not rows:
            return None
        row = rows[0]
        return CurrentRunSummary(
            run_id=str(row[0]),
            loaded_at=cast(Any, row[1]),
            country_count=int(row[2]),
            indicator_count=int(row[3]),
            observation_count=int(row[4]),
            start_year=_optional_int(row[5]),
            end_year=_optional_int(row[6]),
            source_ids=tuple(int(value) for value in cast(Sequence[Any], row[7] or [])),
        )

    def get_countries(
        self,
        countries: Sequence[str],
    ) -> tuple[CountryCatalogEntry, ...]:
        """Return exact country metadata for codes available in the active run."""

        country_codes = self._validate_countries(countries)
        rows = self._query(
            """
            WITH (
                SELECT argMax(run_id, loaded_at)
                FROM etl_run
                WHERE status = 'loaded'
            ) AS current_run
            SELECT
                current_run AS run_id,
                t.country_code AS country_code,
                any(t.country_name) AS country_name,
                any(t.region_name) AS region_name,
                any(t.income_level_name) AS income_level_name,
                any(t.longitude) AS longitude,
                any(t.latitude) AS latitude
            FROM mart_indicator_timeseries AS t
            WHERE t.run_id = current_run
              AND t.country_code IN {countries:Array(String)}
            GROUP BY t.country_code
            ORDER BY t.country_code
            """,
            {"countries": list(country_codes)},
        )
        return tuple(_country_catalog_entry(row) for row in rows)

    def search_countries(
        self,
        *,
        query: str = "",
        region: str | None = None,
        income_level: str | None = None,
        additional_country_codes: Sequence[str] = (),
        limit: int = 20,
    ) -> tuple[CountryCatalogEntry, ...]:
        """Search countries present in the active analytical run.

        Blank discovery requests use a dedicated query path. Besides being simpler, this
        avoids evaluating text-search expressions against an empty search term in the
        live ClickHouse query.
        """

        if not 1 <= limit <= 100:
            raise ValueError("country search limit must be between 1 and 100")

        clean_query = query.strip()
        clean_region = (region or "").strip()
        clean_income_level = (income_level or "").strip()
        extra_codes = tuple(
            code
            for code in dict.fromkeys(
                str(value).strip().upper() for value in additional_country_codes
            )
            if _COUNTRY_RE.fullmatch(code)
        )
        parameters: dict[str, Any] = {
            "region": clean_region,
            "income_level": clean_income_level,
            "limit": limit,
        }

        search_clause = ""
        order_clause = "country_name"
        if clean_query:
            parameters["query"] = clean_query
            parameters["additional_country_codes"] = list(extra_codes)
            search_clause = """
                AND
                (
                    positionCaseInsensitiveUTF8(
                        country_code, {query:String}
                    ) > 0
                    OR positionCaseInsensitiveUTF8(
                        country_name, {query:String}
                    ) > 0
                    OR country_code IN {additional_country_codes:Array(String)}
                )
            """
            order_clause = """
                multiIf(
                    lowerUTF8(country_code) = lowerUTF8({query:String}), 0,
                    lowerUTF8(country_name) = lowerUTF8({query:String}), 1,
                    startsWith(lowerUTF8(country_name), lowerUTF8({query:String})), 2,
                    3
                ),
                country_name
            """

        rows = self._query(
            f"""
            WITH (
                SELECT argMax(run_id, loaded_at)
                FROM etl_run
                WHERE status = 'loaded'
            ) AS current_run
            SELECT *
            FROM
            (
                SELECT
                    current_run AS run_id,
                    t.country_code AS country_code,
                    any(t.country_name) AS country_name,
                    any(t.region_name) AS region_name,
                    any(t.income_level_name) AS income_level_name,
                    any(t.longitude) AS longitude,
                    any(t.latitude) AS latitude
                FROM mart_indicator_timeseries AS t
                WHERE t.run_id = current_run
                GROUP BY t.country_code
            )
            WHERE
                (
                    {{region:String}} = ''
                    OR lowerUTF8(region_name) = lowerUTF8({{region:String}})
                )
                AND (
                    {{income_level:String}} = ''
                    OR lowerUTF8(income_level_name) = lowerUTF8(
                        {{income_level:String}}
                    )
                )
                {search_clause}
            ORDER BY {order_clause}
            LIMIT {{limit:UInt32}}
            """,
            parameters,
        )
        return tuple(_country_catalog_entry(row) for row in rows)

    def search_indicators(
        self,
        *,
        query: str,
        categories: Sequence[str] = (),
        limit: int = 20,
    ) -> tuple[IndicatorCatalogEntry, ...]:
        """Search indicators that are actually available in the active run."""

        clean_query = query.strip()
        if not clean_query:
            raise ValueError("indicator search query cannot be blank")
        if not 1 <= limit <= 100:
            raise ValueError("indicator search limit must be between 1 and 100")
        clean_categories = tuple(
            dict.fromkeys(str(value).strip().lower() for value in categories if str(value).strip())
        )
        category_clause = ""
        if clean_categories:
            category_clause = (
                "AND lowerUTF8(ifNull(indicator_category, '')) IN {categories:Array(String)}"
            )
        parameters: dict[str, Any] = {
            "query": clean_query,
            "categories": list(clean_categories),
            "limit": limit,
        }
        rows = self._query(
            f"""
            WITH (
                SELECT argMax(run_id, loaded_at)
                FROM etl_run
                WHERE status = 'loaded'
            ) AS current_run
            SELECT *
            FROM
            (
                SELECT
                    current_run AS run_id,
                    t.source_id AS source_id,
                    t.indicator_code AS indicator_code,
                    any(t.indicator_alias) AS indicator_alias,
                    any(t.indicator_name) AS indicator_name,
                    any(t.indicator_name_ru) AS indicator_name_ru,
                    any(t.indicator_category) AS indicator_category,
                    any(t.unit) AS unit,
                    any(t.display_unit) AS display_unit,
                    arraySort(groupUniqArray(t.dimensions_json)) AS dimensions_json
                FROM mart_indicator_timeseries AS t
                WHERE t.run_id = current_run
                GROUP BY t.source_id, t.indicator_code
            )
            WHERE
                (
                    positionCaseInsensitiveUTF8(indicator_code, {{query:String}}) > 0
                    OR positionCaseInsensitiveUTF8(
                        ifNull(indicator_alias, ''), {{query:String}}
                    ) > 0
                    OR positionCaseInsensitiveUTF8(indicator_name, {{query:String}}) > 0
                    OR positionCaseInsensitiveUTF8(
                        ifNull(indicator_name_ru, ''), {{query:String}}
                    ) > 0
                )
                {category_clause}
            ORDER BY
                multiIf(
                    lowerUTF8(indicator_code) = lowerUTF8({{query:String}}), 0,
                    lowerUTF8(ifNull(indicator_alias, '')) = lowerUTF8({{query:String}}), 1,
                    2
                ),
                indicator_name
            LIMIT {{limit:UInt32}}
            """,
            parameters,
        )
        return tuple(_indicator_catalog_entry(row) for row in rows)

    def resolve_metrics(
        self,
        metrics: Sequence[str | MetricRequest],
    ) -> tuple[ResolvedMetric, ...]:
        """Resolve aliases/codes/source-code pairs against the current run."""

        requests = tuple(_metric_request(metric) for metric in metrics)
        if not requests:
            raise ValueError("at least one metric is required")
        if len(requests) > self.max_metrics:
            raise ValueError(f"at most {self.max_metrics} metrics are allowed")

        resolved: list[ResolvedMetric] = []
        seen: set[tuple[int, str, str]] = set()
        for request in requests:
            metric = self._resolve_metric(request)
            identity = (metric.source_id, metric.indicator_code, metric.dimensions_json)
            if identity not in seen:
                resolved.append(metric)
                seen.add(identity)
        return tuple(resolved)

    def get_timeseries(
        self,
        *,
        countries: Sequence[str],
        metrics: Sequence[str | MetricRequest],
        start_year: int | None = None,
        end_year: int | None = None,
    ) -> TimeseriesResult:
        """Return bounded country/metric time series with evidence and coverage."""

        country_codes = self._validate_countries(countries)
        resolved = self.resolve_metrics(metrics)
        self._validate_period(start_year, end_year)

        parameters: dict[str, Any] = {"countries": list(country_codes)}
        metric_sql = _metric_predicate(resolved, parameters)
        clauses = ["country_code IN {countries:Array(String)}", f"({metric_sql})"]
        if start_year is not None:
            clauses.append("year >= {start_year:Int32}")
            parameters["start_year"] = start_year
        if end_year is not None:
            clauses.append("year <= {end_year:Int32}")
            parameters["end_year"] = end_year
        parameters["row_limit"] = self.max_result_rows + 1

        query = f"""
            SELECT
                run_id,
                source_id,
                indicator_code,
                indicator_alias,
                indicator_name,
                indicator_name_ru,
                indicator_category,
                unit,
                display_unit,
                country_code,
                country_name,
                region_name,
                income_level_name,
                year,
                value,
                dimensions_json,
                is_missing
            FROM mart_indicator_timeseries
            WHERE {" AND ".join(clauses)}
            ORDER BY
                country_code,
                source_id,
                indicator_code,
                dimensions_json,
                year
            LIMIT {{row_limit:UInt32}}
        """
        rows = self._query(query, parameters)
        if len(rows) > self.max_result_rows:
            raise ResultLimitError(
                f"time-series query exceeds {self.max_result_rows} rows; narrow the scope"
            )

        points = tuple(_timeseries_point(row) for row in rows)
        coverage = _build_coverage(
            country_codes,
            resolved,
            points,
            start_year=start_year,
            end_year=end_year,
        )
        warnings: list[str] = []
        if not points:
            warnings.append("The requested scope contains no observations.")
        incomplete = sum(item.coverage_ratio < 1 for item in coverage)
        if incomplete:
            warnings.append(f"{incomplete} series contain missing years or null values.")

        run_ids = {metric.run_id for metric in resolved}
        return TimeseriesResult(
            run_id=next(iter(run_ids)) if len(run_ids) == 1 else None,
            countries=country_codes,
            metrics=resolved,
            start_year=start_year,
            end_year=end_year,
            points=points,
            coverage=coverage,
            warnings=tuple(warnings),
        )

    def get_country_snapshot(
        self,
        *,
        countries: Sequence[str],
        metrics: Sequence[str | MetricRequest],
        mode: SnapshotMode = "latest_available",
        year: int | None = None,
    ) -> CountrySnapshotResult:
        """Return latest, latest-common-year, or fixed-year values."""

        country_codes = self._validate_countries(countries)
        resolved = self.resolve_metrics(metrics)
        comparison_year: int | None
        if mode == "year":
            if year is None:
                raise ValueError("year is required when mode='year'")
            self._validate_period(year, year)
            comparison_year = year
        elif year is not None:
            raise ValueError("year can only be supplied when mode='year'")
        elif mode == "common_year":
            comparison_year = self._latest_common_year(country_codes, resolved)
        elif mode == "latest_available":
            comparison_year = None
        else:
            raise ValueError(f"unsupported snapshot mode: {mode}")

        parameters: dict[str, Any] = {"countries": list(country_codes)}
        metric_sql = _metric_predicate(resolved, parameters)
        parameters["row_limit"] = self.max_result_rows + 1

        if mode == "latest_available":
            query = f"""
                SELECT
                    run_id,
                    source_id,
                    indicator_code,
                    indicator_alias,
                    indicator_name,
                    indicator_name_ru,
                    indicator_category,
                    unit,
                    display_unit,
                    country_code,
                    country_name,
                    region_name,
                    income_level_name,
                    observation_year,
                    value,
                    dimensions_json
                FROM mart_country_snapshot
                WHERE country_code IN {{countries:Array(String)}}
                  AND ({metric_sql})
                ORDER BY country_code, source_id, indicator_code, dimensions_json
                LIMIT {{row_limit:UInt32}}
            """
        elif comparison_year is None:
            query = "SELECT NULL WHERE 0"
        else:
            parameters["comparison_year"] = comparison_year
            query = f"""
                SELECT
                    run_id,
                    source_id,
                    indicator_code,
                    indicator_alias,
                    indicator_name,
                    indicator_name_ru,
                    indicator_category,
                    unit,
                    display_unit,
                    country_code,
                    country_name,
                    region_name,
                    income_level_name,
                    year AS observation_year,
                    value,
                    dimensions_json
                FROM mart_indicator_timeseries
                WHERE country_code IN {{countries:Array(String)}}
                  AND ({metric_sql})
                  AND year = {{comparison_year:Int32}}
                ORDER BY country_code, source_id, indicator_code, dimensions_json
                LIMIT {{row_limit:UInt32}}
            """

        should_query = comparison_year is not None or mode == "latest_available"
        rows = self._query(query, parameters) if should_query else []
        if len(rows) > self.max_result_rows:
            raise ResultLimitError(
                f"snapshot query exceeds {self.max_result_rows} rows; narrow the scope"
            )
        points = tuple(_snapshot_point(row) for row in rows)

        expected = {
            (country, metric.source_id, metric.indicator_code, metric.dimensions_json)
            for country in country_codes
            for metric in resolved
        }
        available = {
            (point.country_code, point.source_id, point.indicator_code, point.dimensions_json)
            for point in points
            if point.value is not None
        }
        missing_pairs = tuple(
            f"{country}:{source_id}:{code}:{dimensions}"
            for country, source_id, code, dimensions in sorted(expected - available)
        )
        warnings: list[str] = []
        if mode == "common_year" and comparison_year is None:
            warnings.append("No year has non-null values for every requested country/metric pair.")
        if missing_pairs:
            warnings.append(f"{len(missing_pairs)} requested country/metric pairs are missing.")

        return CountrySnapshotResult(
            mode=mode,
            comparison_year=comparison_year,
            countries=country_codes,
            metrics=resolved,
            points=points,
            missing_pairs=missing_pairs,
            warnings=tuple(warnings),
        )

    def get_data_quality(
        self,
        *,
        countries: Sequence[str],
        metrics: Sequence[str | MetricRequest],
    ) -> DataQualityResult:
        """Return coverage metadata for requested country/metric series."""

        country_codes = self._validate_countries(countries)
        resolved = self.resolve_metrics(metrics)
        parameters: dict[str, Any] = {"countries": list(country_codes)}
        metric_sql = _metric_predicate(resolved, parameters)
        parameters["row_limit"] = self.max_result_rows + 1
        query = f"""
            SELECT
                run_id,
                source_id,
                indicator_code,
                indicator_alias,
                indicator_name,
                indicator_category,
                country_code,
                country_name,
                dimensions_json,
                row_count,
                non_null_count,
                null_count,
                expected_years,
                coverage_ratio,
                first_available_year,
                latest_available_year
            FROM mart_data_quality
            WHERE country_code IN {{countries:Array(String)}}
              AND ({metric_sql})
            ORDER BY country_code, source_id, indicator_code
            LIMIT {{row_limit:UInt32}}
        """
        rows = self._query(query, parameters)
        if len(rows) > self.max_result_rows:
            raise ResultLimitError(
                f"data-quality query exceeds {self.max_result_rows} rows; narrow the scope"
            )
        entries = tuple(_data_quality_entry(row) for row in rows)
        expected_count = len(country_codes) * len(resolved)
        warnings: list[str] = []
        if len(entries) < expected_count:
            warnings.append(
                f"Data-quality mart returned {len(entries)} of {expected_count} expected rows."
            )
        incomplete = sum(entry.coverage_ratio < 1 for entry in entries)
        if incomplete:
            warnings.append(f"{incomplete} series have coverage below 100%.")
        return DataQualityResult(
            countries=country_codes,
            metrics=resolved,
            entries=entries,
            warnings=tuple(warnings),
        )

    def _resolve_metric(self, request: MetricRequest) -> ResolvedMetric:
        explicit = _EXPLICIT_METRIC_RE.fullmatch(request.selector)
        parameters: dict[str, Any] = {}
        if explicit:
            parameters["source_id"] = int(explicit.group("source"))
            parameters["indicator_code"] = explicit.group("code")
            condition = (
                "t.source_id = {source_id:Int32} AND t.indicator_code = {indicator_code:String}"
            )
        else:
            parameters["selector"] = request.selector
            condition = (
                "(t.indicator_alias = {selector:String} "
                "OR t.indicator_code = {selector:String} "
                "OR m.wide_column = {selector:String})"
            )

        query = f"""
            WITH (
                SELECT argMax(run_id, loaded_at)
                FROM etl_run
                WHERE status = 'loaded'
            ) AS current_run
            SELECT
                current_run AS run_id,
                t.source_id AS source_id,
                t.indicator_code AS indicator_code,
                any(t.indicator_alias) AS indicator_alias,
                any(t.indicator_name) AS indicator_name,
                any(t.indicator_name_ru) AS indicator_name_ru,
                any(t.indicator_category) AS indicator_category,
                any(t.unit) AS unit,
                any(t.display_unit) AS display_unit,
                t.dimensions_json AS dimensions_json
            FROM mart_indicator_timeseries AS t
            LEFT JOIN mart_metric_catalog AS m
                ON m.run_id = t.run_id
               AND toInt32(m.source_id) = t.source_id
               AND m.indicator_code = t.indicator_code
               AND ifNull(m.dimension_signature, '{{}}') = t.dimensions_json
            WHERE t.run_id = current_run
              AND {condition}
            GROUP BY t.source_id, t.indicator_code, t.dimensions_json
            ORDER BY t.source_id, t.indicator_code, t.dimensions_json
        """
        rows = self._query(query, parameters)
        candidates = tuple(_resolved_metric(row) for row in rows)
        if not candidates:
            raise MetricNotFoundError(
                f"metric selector is absent from the active ClickHouse run: {request.selector}"
            )

        matching_dimensions = tuple(
            metric
            for metric in candidates
            if _dimensions_match(metric.dimensions_json, request.dimensions)
        )
        if not matching_dimensions:
            available_values = sorted({metric.dimensions_json for metric in candidates})
            available = ", ".join(available_values)
            if not request.dimensions and all(value != "{}" for value in available_values):
                raise DimensionRequiredError(
                    f"metric {request.selector} requires an explicit dimension slice; "
                    f"available: {available}"
                )
            raise MetricNotFoundError(
                f"metric {request.selector} does not expose dimensions {request.dimensions}; "
                f"available: {available}"
            )
        identities = {(metric.source_id, metric.indicator_code) for metric in matching_dimensions}
        if len(identities) > 1:
            available = ", ".join(f"{source_id}:{code}" for source_id, code in sorted(identities))
            raise AmbiguousMetricError(
                f"metric selector {request.selector!r} is ambiguous; use one of: {available}"
            )
        return matching_dimensions[0]

    def _latest_common_year(
        self,
        countries: tuple[str, ...],
        metrics: tuple[ResolvedMetric, ...],
    ) -> int | None:
        parameters: dict[str, Any] = {
            "countries": list(countries),
            "expected_pairs": len(countries) * len(metrics),
        }
        metric_sql = _metric_predicate(metrics, parameters)
        query = f"""
            SELECT nullIf(max(year), 0)
            FROM
            (
                SELECT year
                FROM mart_indicator_timeseries
                WHERE country_code IN {{countries:Array(String)}}
                  AND ({metric_sql})
                  AND value IS NOT NULL
                GROUP BY year
                HAVING uniqExact(
                    tuple(country_code, source_id, indicator_code, dimensions_json)
                ) = {{expected_pairs:UInt32}}
            )
        """
        rows = self._query(query, parameters)
        if not rows or not rows[0] or rows[0][0] is None:
            return None
        return int(rows[0][0])

    def _validate_countries(self, countries: Sequence[str]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(str(value).strip().upper() for value in countries))
        if not normalized:
            raise ValueError("at least one country is required")
        if len(normalized) > self.max_countries:
            raise ValueError(f"at most {self.max_countries} countries are allowed")
        invalid = [value for value in normalized if not _COUNTRY_RE.fullmatch(value)]
        if invalid:
            raise ValueError(f"invalid ISO3 country codes: {', '.join(invalid)}")
        return normalized

    def _validate_period(self, start_year: int | None, end_year: int | None) -> None:
        for value in (start_year, end_year):
            if value is not None and not 1800 <= value <= 2200:
                raise ValueError("years must be between 1800 and 2200")
        if start_year is not None and end_year is not None:
            if end_year < start_year:
                raise ValueError("end_year must be greater than or equal to start_year")
            if end_year - start_year + 1 > self.max_years:
                raise ValueError(f"at most {self.max_years} years are allowed")

    def _query(
        self,
        query: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> list[list[Any] | tuple[Any, ...]]:
        return self._client.query(query, parameters=parameters).result_set


def _dimensions_match(candidate_json: str, requested: Mapping[str, str]) -> bool:
    try:
        raw = json.loads(candidate_json)
    except json.JSONDecodeError:
        return False
    if not isinstance(raw, dict) or set(raw) != set(requested):
        return False
    for key, requested_value in requested.items():
        candidate = raw.get(key)
        if isinstance(candidate, dict):
            identifiers = {str(candidate.get("id", "")), str(candidate.get("value", ""))}
            if requested_value not in identifiers:
                return False
        elif str(candidate) != requested_value:
            return False
    return True


def _metric_request(value: str | MetricRequest) -> MetricRequest:
    return value if isinstance(value, MetricRequest) else MetricRequest(selector=str(value))


def _metric_predicate(
    metrics: Sequence[ResolvedMetric],
    parameters: dict[str, Any],
) -> str:
    conditions: list[str] = []
    for index, metric in enumerate(metrics):
        source_key = f"metric_source_{index}"
        code_key = f"metric_code_{index}"
        dimensions_key = f"metric_dimensions_{index}"
        parameters[source_key] = metric.source_id
        parameters[code_key] = metric.indicator_code
        parameters[dimensions_key] = metric.dimensions_json
        parts = [
            f"source_id = {{{source_key}:Int32}}",
            f"indicator_code = {{{code_key}:String}}",
        ]
        parts.append(f"dimensions_json = {{{dimensions_key}:String}}")
        conditions.append("(" + " AND ".join(parts) + ")")
    return " OR ".join(conditions)


def _country_catalog_entry(row: Sequence[Any]) -> CountryCatalogEntry:
    return CountryCatalogEntry(
        run_id=str(row[0]),
        country_code=str(row[1]),
        country_name=str(row[2]),
        region_name=_optional_str(row[3]),
        income_level_name=_optional_str(row[4]),
        longitude=_optional_float(row[5]),
        latitude=_optional_float(row[6]),
    )


def _indicator_catalog_entry(row: Sequence[Any]) -> IndicatorCatalogEntry:
    dimensions = cast(Sequence[Any], row[9] or [])
    return IndicatorCatalogEntry(
        run_id=str(row[0]),
        source_id=int(row[1]),
        indicator_code=str(row[2]),
        alias=_optional_str(row[3]),
        indicator_name=str(row[4]),
        indicator_name_ru=_optional_str(row[5]),
        category=_optional_str(row[6]),
        unit=_optional_str(row[7]),
        display_unit=_optional_str(row[8]),
        dimensions_json=tuple(str(value) for value in dimensions),
    )


def _resolved_metric(row: Sequence[Any]) -> ResolvedMetric:
    return ResolvedMetric(
        run_id=str(row[0]),
        source_id=int(row[1]),
        indicator_code=str(row[2]),
        alias=_optional_str(row[3]),
        indicator_name=str(row[4]),
        indicator_name_ru=_optional_str(row[5]),
        category=_optional_str(row[6]),
        unit=_optional_str(row[7]),
        display_unit=_optional_str(row[8]),
        dimensions_json=str(row[9]),
    )


def _timeseries_point(row: Sequence[Any]) -> TimeseriesPoint:
    return TimeseriesPoint(
        run_id=str(row[0]),
        source_id=int(row[1]),
        indicator_code=str(row[2]),
        indicator_alias=_optional_str(row[3]),
        indicator_name=str(row[4]),
        indicator_name_ru=_optional_str(row[5]),
        indicator_category=_optional_str(row[6]),
        unit=_optional_str(row[7]),
        display_unit=_optional_str(row[8]),
        country_code=str(row[9]),
        country_name=str(row[10]),
        region_name=_optional_str(row[11]),
        income_level_name=_optional_str(row[12]),
        year=int(row[13]),
        value=_optional_float(row[14]),
        dimensions_json=str(row[15]),
        is_missing=bool(row[16]),
    )


def _snapshot_point(row: Sequence[Any]) -> SnapshotPoint:
    return SnapshotPoint(
        run_id=str(row[0]),
        source_id=int(row[1]),
        indicator_code=str(row[2]),
        indicator_alias=_optional_str(row[3]),
        indicator_name=str(row[4]),
        indicator_name_ru=_optional_str(row[5]),
        indicator_category=_optional_str(row[6]),
        unit=_optional_str(row[7]),
        display_unit=_optional_str(row[8]),
        country_code=str(row[9]),
        country_name=str(row[10]),
        region_name=_optional_str(row[11]),
        income_level_name=_optional_str(row[12]),
        observation_year=_optional_int(row[13]),
        value=_optional_float(row[14]),
        dimensions_json=str(row[15]),
    )


def _data_quality_entry(row: Sequence[Any]) -> DataQualityEntry:
    return DataQualityEntry(
        run_id=str(row[0]),
        source_id=int(row[1]),
        indicator_code=str(row[2]),
        indicator_alias=_optional_str(row[3]),
        indicator_name=str(row[4]),
        indicator_category=_optional_str(row[5]),
        country_code=str(row[6]),
        country_name=str(row[7]),
        dimensions_json=str(row[8]),
        row_count=int(row[9]),
        non_null_count=int(row[10]),
        null_count=int(row[11]),
        expected_years=int(row[12]),
        coverage_ratio=float(row[13]),
        first_available_year=_optional_int(row[14]),
        latest_available_year=_optional_int(row[15]),
    )


def _build_coverage(
    countries: tuple[str, ...],
    metrics: tuple[ResolvedMetric, ...],
    points: tuple[TimeseriesPoint, ...],
    *,
    start_year: int | None,
    end_year: int | None,
) -> tuple[SeriesCoverage, ...]:
    grouped: dict[tuple[str, int, str, str], list[TimeseriesPoint]] = {}
    for point in points:
        grouped.setdefault(point.series_key, []).append(point)

    output: list[SeriesCoverage] = []
    for country in countries:
        for metric in metrics:
            key = (country, metric.source_id, metric.indicator_code, metric.dimensions_json)
            series = grouped.get(key, [])
            observed_years = {point.year for point in series if point.value is not None}
            row_years = {point.year for point in series}
            first = (
                start_year if start_year is not None else (min(row_years) if row_years else None)
            )
            last = end_year if end_year is not None else (max(row_years) if row_years else None)
            expected = (
                set(range(first, last + 1)) if first is not None and last is not None else set()
            )
            output.append(
                SeriesCoverage(
                    country_code=country,
                    source_id=metric.source_id,
                    indicator_code=metric.indicator_code,
                    dimensions_json=metric.dimensions_json,
                    expected_years=len(expected),
                    row_count=len(row_years),
                    non_null_count=len(observed_years),
                    coverage_ratio=(len(observed_years) / len(expected) if expected else 0.0),
                    missing_years=tuple(sorted(expected - observed_years)),
                )
            )
    return tuple(output)


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: object) -> float | None:
    return None if value is None else float(cast(Any, value))


def _optional_int(value: object) -> int | None:
    return None if value is None else int(cast(Any, value))
