# Архитектура WB Insight Agent

## Текущий вертикальный срез

В проекте реализован первый воспроизводимый data pipeline:

```text
World Bank API
      ↓
WorldBankClient
      ↓
RawStore
      ↓
normalize_countries / normalize_indicators / normalize_observations
      ↓
semantic enrichment (registry + World Bank metadata)
      ↓
quality checks
      ↓
Parquet + quality_report.json
```

Этот слой должен быть полностью работоспособным без Airflow, ClickHouse, DataLens и LLM.

## Архитектурные слои

```text
1. Ingestion
   World Bank API, pagination, retries

2. Storage and transformation
   append-only raw data, normalization, semantic registry, quality checks, Parquet

3. Analytical storage
   Object Storage, ClickHouse, staging and marts

4. Analytics
   dynamics, country comparison, peer benchmark, correlations

5. Agent tools
   typed read-only functions over analytical marts

6. Agent and API
   tool orchestration, verifier, backend and user interface
```

## Правило зависимостей

```text
agent → tools → analytics → analytical storage → transforms/storage → ingestion
```

- HTTP client не содержит pandas-логику;
- transforms не обращаются в интернет и не знают об LLM;
- quality checks работают на нормализованных таблицах;
- pipeline связывает компоненты, но не хранит аналитическую бизнес-логику;
- notebooks используют функции пакета, а не дублируют ETL;
- агент в будущем получает только типизированные read-only tools, а не произвольный SQL.

## Raw layer

Локальный `RawStore` использует append-only layout:

```text
data/raw/{dataset}/load_date=YYYY-MM-DD/run_id={run_id}/{dataset}.json
```

JSON содержит:

- dataset;
- run_id;
- fetched_at;
- record_count;
- request parameters;
- records returned by `WorldBankClient`.

Для observations raw-артефакты разделяются по World Bank source, например
`observations_source_2` и `observations_source_6`. Это сохраняет исходные API-записи
без добавления служебного `source_id` внутрь ответа и одновременно не теряет provenance.

Этот layout специально близок к будущим object-storage prefixes. Поэтому локальную реализацию можно заменить Yandex Object Storage backend без изменения transforms.

## Processed layer

```text
data/processed/run_id={run_id}/
├── countries.parquet
├── indicators.parquet
├── observations.parquet
└── quality_report.json
```

Основной ключ observations:

```text
source_id + indicator_code + country_code + year + dimensions_json
```

Для classic sources `dimensions_json = {}`. Для multidimensional sources дополнительные
concepts сохраняются как канонический JSON, например IDS:

```json
{"Counterpart-Area":{"id":"WLD","value":"World"}}
```

Пропущенное значение показателя сохраняется как `null` и не заменяется нулем.

### Indicator semantics

`configs/indicators.yaml` является semantic registry, а не allowlist. Индикатор
идентифицируется парой `(source_id, indicator_code)`. Верхнеуровневый `source_id` в
registry является default source, а отдельная запись может задать собственный `source_id`.

CLI принимает aliases, обычные коды и `SOURCE_ID:INDICATOR_CODE`. Незарегистрированный
обычный код относится к default source registry (обычно WDI/source 2). Индикаторы других
баз указываются как `SOURCE_ID:CODE`. Несколько sources могут участвовать в одном ingestion run.

Для каждого выбранного source pipeline получает `/sources/{id}/concepts/data` и его Series
catalog. Источник с concepts `Country/Series/Time` обрабатывается classic adapter. Если есть
дополнительные dimensions, используется Advanced Data API. IDS/source 6 автоматически
получает `Counterpart-Area=WLD`, если пользователь не передал другой `--dimensions` filter.
Другие неоднозначные дополнительные dimensions требуют явного выбора, чтобы pipeline не
агрегировал и не смешивал многомерные значения молча.

Для зарегистрированных показателей registry задает стабильные `alias`, `category`,
`unit` и `display_unit`. Для незарегистрированных показателей category берется из
World Bank topics, если они присутствуют. Unit используется из API только при
явном наличии; отсутствующая единица остается `null` и фиксируется quality warning.

Это позволяет безопасно работать с произвольным набором индикаторов, не выдавая
эвристически угаданную единицу измерения за достоверную.

## Quality gate

Blocking checks:

- required columns;
- dataset non-empty;
- unique keys including `dimensions_json`;
- configured countries and `(source_id, indicator_code)` pairs exist;
- observations stay inside requested country/source/indicator scope;
- years stay inside requested period;
- aggregates do not survive filtering when disabled.

Warnings:

- null metric values;
- missing keys in expected `source × country × indicator × year` grid;
- missing reliable unit metadata for selected indicators;
- missing category metadata for selected indicators.

Raw data сохраняются до quality gate. При провале проверки processed Parquet не создается, но `quality_report.json` остается для диагностики.

## Будущая привязка к Yandex Cloud

```text
Airflow DAG                 → Managed Service for Apache Airflow
RawStore backend            → Yandex Object Storage
Analytical repository       → Managed Service for ClickHouse
Analytical marts            → Yandex DataLens
Agent backend               → Serverless Containers / API Gateway
LLM orchestration           → Yandex Cloud AI Studio
Secrets                     → Yandex Lockbox
```

Следующий архитектурный этап — вынести persistent storage в Object Storage/ClickHouse, не меняя уже протестированные transformations и quality rules.
