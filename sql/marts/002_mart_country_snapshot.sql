CREATE OR REPLACE VIEW mart_country_snapshot AS
WITH
    (SELECT argMax(run_id, loaded_at) FROM etl_run WHERE status = 'loaded') AS current_run,
    latest AS
    (
        SELECT
            o.run_id AS run_id,
            o.source_id AS source_id,
            o.indicator_code AS indicator_code,
            o.country_code AS country_code,
            o.dimensions_json AS dimensions_json,
            if(
                countIf(o.value IS NOT NULL) = 0,
                CAST(NULL AS Nullable(Float64)),
                argMaxIf(o.value, o.year, o.value IS NOT NULL)
            ) AS latest_value,
            nullIf(maxIf(o.year, o.value IS NOT NULL), 0) AS observation_year
        FROM fact_observation AS o
        WHERE o.run_id = current_run
        GROUP BY
            o.run_id,
            o.source_id,
            o.indicator_code,
            o.country_code,
            o.dimensions_json
    )
SELECT
    l.run_id AS run_id,
    l.source_id AS source_id,
    l.indicator_code AS indicator_code,
    i.alias AS indicator_alias,
    i.indicator_name AS indicator_name,
    i.name_ru AS indicator_name_ru,
    i.category AS indicator_category,
    i.unit AS unit,
    i.display_unit AS display_unit,
    l.country_code AS country_code,
    c.country_name AS country_name,
    c.region_name AS region_name,
    c.income_level_name AS income_level_name,
    c.longitude AS longitude,
    c.latitude AS latitude,
    l.observation_year AS observation_year,
    l.latest_value AS value,
    l.dimensions_json AS dimensions_json
FROM latest AS l
LEFT JOIN dim_country AS c
    ON c.run_id = l.run_id AND c.country_code = l.country_code
LEFT JOIN dim_indicator AS i
    ON i.run_id = l.run_id
   AND i.source_id = l.source_id
   AND i.indicator_code = l.indicator_code
