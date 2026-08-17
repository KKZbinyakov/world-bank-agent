CREATE OR REPLACE VIEW mart_country_year AS
SELECT
    t.run_id AS run_id,
    t.country_code AS country_code,
    t.country_name AS country_name,
    t.region_name AS region_name,
    t.income_level_name AS income_level_name,
    t.longitude AS longitude,
    t.latitude AS latitude,
    t.year AS year,
    t.source_id AS source_id,
    t.indicator_code AS indicator_code,
    t.indicator_alias AS indicator_alias,
    t.indicator_name AS indicator_name,
    t.indicator_name_ru AS indicator_name_ru,
    t.indicator_category AS indicator_category,
    t.unit AS unit,
    t.display_unit AS display_unit,
    t.value AS value,
    t.dimensions_json AS dimensions_json,
    t.is_missing AS is_missing
FROM mart_indicator_timeseries AS t
