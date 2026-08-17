CREATE TABLE IF NOT EXISTS etl_run
(
    run_id String,
    loaded_at DateTime64(3, 'UTC'),
    source_path String,
    countries UInt32,
    indicators UInt32,
    observations UInt64,
    wide_rows Nullable(UInt64),
    status LowCardinality(String)
)
ENGINE = MergeTree
ORDER BY (loaded_at, run_id)
