CREATE OR REPLACE VIEW mart_indicator_timeseries AS
WITH
    (SELECT argMax(run_id, loaded_at) FROM etl_run WHERE status = 'loaded') AS current_run
SELECT
    o.run_id AS run_id,
    o.source_id AS source_id,
    o.indicator_code AS indicator_code,
    coalesce(i.alias, o.indicator_alias) AS indicator_alias,
    i.indicator_name AS indicator_name,
    i.name_ru AS indicator_name_ru,
    i.category AS indicator_category,
    i.unit AS unit,
    i.display_unit AS display_unit,
    o.country_code AS country_code,
    c.country_name AS country_name,
    c.region_name AS region_name,
    c.income_level_name AS income_level_name,
    c.longitude AS longitude,
    c.latitude AS latitude,
    o.year AS year,
    o.value AS value,
    o.dimensions_json AS dimensions_json,
    o.dimension_count AS dimension_count,
    o.is_missing AS is_missing,
    o.loaded_at AS loaded_at
FROM fact_observation AS o
LEFT JOIN dim_country AS c
    ON c.run_id = o.run_id AND c.country_code = o.country_code
LEFT JOIN dim_indicator AS i
    ON i.run_id = o.run_id
   AND i.source_id = o.source_id
   AND i.indicator_code = o.indicator_code
WHERE o.run_id = current_run
