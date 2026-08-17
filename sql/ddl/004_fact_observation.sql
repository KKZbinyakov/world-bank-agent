CREATE TABLE IF NOT EXISTS fact_observation
(
    source_id Int32,
    indicator_code String,
    indicator_name String,
    indicator_alias Nullable(String),
    indicator_name_ru Nullable(String),
    indicator_category Nullable(String),
    category_source Nullable(String),
    indicator_role Nullable(String),
    country_code String,
    country_name String,
    year Int16,
    value Nullable(Float64),
    dimensions_json String,
    dimension_count UInt16,
    source_unit Nullable(String),
    unit Nullable(String),
    display_unit Nullable(String),
    unit_source Nullable(String),
    is_registered UInt8,
    observation_status String,
    decimal_scale Nullable(Int16),
    is_missing UInt8,
    run_id String,
    loaded_at DateTime64(3, 'UTC')
)
ENGINE = MergeTree
PARTITION BY year
ORDER BY (run_id, source_id, indicator_code, country_code, year, dimensions_json)
