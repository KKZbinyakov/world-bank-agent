CREATE OR REPLACE VIEW mart_data_quality AS
WITH
    (SELECT argMax(run_id, loaded_at) FROM etl_run WHERE status = 'loaded') AS current_run,
    period AS
    (
        SELECT
            min(year) AS min_year,
            max(year) AS max_year
        FROM fact_observation
        WHERE run_id = current_run
    )
SELECT
    o.run_id AS run_id,
    o.source_id AS source_id,
    o.indicator_code AS indicator_code,
    i.alias AS indicator_alias,
    i.indicator_name AS indicator_name,
    i.category AS indicator_category,
    o.country_code AS country_code,
    c.country_name AS country_name,
    o.dimensions_json AS dimensions_json,
    count() AS row_count,
    countIf(o.value IS NOT NULL) AS non_null_count,
    countIf(o.value IS NULL) AS null_count,
    period.max_year - period.min_year + 1 AS expected_years,
    round(countIf(o.value IS NOT NULL) / (period.max_year - period.min_year + 1), 4)
        AS coverage_ratio,
    nullIf(minIf(o.year, o.value IS NOT NULL), 0) AS first_available_year,
    nullIf(maxIf(o.year, o.value IS NOT NULL), 0) AS latest_available_year
FROM fact_observation AS o
CROSS JOIN period
LEFT JOIN dim_country AS c
    ON c.run_id = o.run_id AND c.country_code = o.country_code
LEFT JOIN dim_indicator AS i
    ON i.run_id = o.run_id
   AND i.source_id = o.source_id
   AND i.indicator_code = o.indicator_code
WHERE o.run_id = current_run
GROUP BY
    o.run_id,
    o.source_id,
    o.indicator_code,
    i.alias,
    i.indicator_name,
    i.category,
    o.country_code,
    c.country_name,
    o.dimensions_json,
    period.min_year,
    period.max_year
