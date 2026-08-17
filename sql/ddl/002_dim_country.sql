CREATE TABLE IF NOT EXISTS dim_country
(
    country_code String,
    iso2_code String,
    country_name String,
    region_code String,
    region_name String,
    admin_region_code String,
    admin_region_name String,
    income_level_code String,
    income_level_name String,
    lending_type_code String,
    lending_type_name String,
    capital_city String,
    longitude Nullable(Float64),
    latitude Nullable(Float64),
    is_aggregate UInt8,
    run_id String,
    loaded_at DateTime64(3, 'UTC')
)
ENGINE = MergeTree
ORDER BY (run_id, country_code)
