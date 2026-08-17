CREATE TABLE IF NOT EXISTS dim_indicator
(
    source_id Int32,
    source_name String,
    indicator_code String,
    indicator_name String,
    source_unit Nullable(String),
    alias Nullable(String),
    name_ru Nullable(String),
    category Nullable(String),
    category_source Nullable(String),
    role Nullable(String),
    unit Nullable(String),
    display_unit Nullable(String),
    unit_source Nullable(String),
    is_registered UInt8,
    source_note String,
    source_organization String,
    topic_ids String,
    topic_names String,
    run_id String,
    loaded_at DateTime64(3, 'UTC')
)
ENGINE = MergeTree
ORDER BY (run_id, source_id, indicator_code)
