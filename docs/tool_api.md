# WB Insight Tool API

Tool API — версионированная read-only HTTP-граница между детерминированным analytical core и будущим агентом Yandex Cloud AI Studio.

```text
HTTP request
     ↓
FastAPI + Pydantic validation
     ↓
ToolService
     ↓
AnalyticalRepository
     ↓
ClickHouse analytical marts
     ↓
typed result + evidence + warnings
```

API не принимает SQL, имена таблиц, DDL или DML. Клиент передаёт только страны, metric selectors, годы и точные dimension slices.

## Запуск

В `.env` должны быть настроены read-only параметры ClickHouse:

```dotenv
CLICKHOUSE_HOST=localhost
CLICKHOUSE_PORT=8123
CLICKHOUSE_DATABASE=wb_insight
CLICKHOUSE_USER=wb_insight
CLICKHOUSE_PASSWORD=wb_insight_local
CLICKHOUSE_SECURE=false

API_HOST=0.0.0.0
API_PORT=8000
API_DOCS_ENABLED=true
MARTS_CONFIG_PATH=configs/marts.yaml
```

Запуск через console script:

```powershell
.\.venv\Scripts\wb-insight-api.exe
```

или через Python:

```powershell
.\.venv\Scripts\python.exe -m wb_insight.api.main
```

Переменная окружения `PORT` имеет приоритет над `API_PORT`. Это потребуется при последующем развёртывании в serverless runtime.

Интерактивная документация:

```text
http://127.0.0.1:8000/docs
```

OpenAPI:

```text
http://127.0.0.1:8000/openapi.json
```

## Public endpoints

### Health и metadata

| Method | Path | Назначение |
|---|---|---|
| GET | `/health/live` | Процесс API работает; ClickHouse не запрашивается |
| GET | `/health/ready` | ClickHouse, обязательные marts и active run доступны |
| GET | `/v1/meta/current-run` | Активный `run_id`, scope, период и sources |

### Tool endpoints

| Method | Path | `operation_id` |
|---|---|---|
| POST | `/v1/tools/search-countries` | `search_countries_v1` |
| POST | `/v1/tools/search-indicators` | `search_indicators_v1` |
| POST | `/v1/tools/timeseries` | `get_timeseries_v1` |
| POST | `/v1/tools/country-snapshot` | `get_country_snapshot_v1` |
| POST | `/v1/tools/trend` | `calculate_trend_v1` |
| POST | `/v1/tools/compare-countries` | `compare_countries_v1` |
| POST | `/v1/tools/correlation` | `calculate_correlation_v1` |
| POST | `/v1/tools/data-quality` | `get_data_quality_v1` |

Stable `operation_id` будут использоваться при преобразовании OpenAPI операций в инструменты модели.

## Metric selectors

Канонический selector — поле `metric_key`, которое возвращает
`POST /v1/tools/search-indicators`. Оно имеет формат `SOURCE_ID:CODE` и не
зависит от presentation-конфига:

```json
"2:NY.GDP.PCAP.CD"
```

Также принимаются semantic alias, однозначный World Bank code и
`wide_column` из `mart_metric_catalog`:

```json
"gdp_per_capita"
```

```json
"NY.GDP.PCAP.CD"
```

```json
"gdp_per_capita_current_usd"
```

Для multidimensional indicator используется объект:

```json
{
  "selector": "6:DT.DOD.DECT.CD",
  "dimensions": {
    "Counterpart-Area": "WLD"
  }
}
```

API не агрегирует dimension slices молча. Если у показателя нет базового среза `{}`, ответ будет `409 dimension_required`.

## Примеры

### Поиск страны

```powershell
Invoke-RestMethod `
    -Method Post `
    -Uri "http://127.0.0.1:8000/v1/tools/search-countries" `
    -ContentType "application/json" `
    -Body '{"query":"Германия","limit":10}'
```

Русские labels берутся из `configs/marts.yaml`, а в ClickHouse страна всегда идентифицируется ISO3-кодом.

### Поиск индикатора

```powershell
Invoke-RestMethod `
    -Method Post `
    -Uri "http://127.0.0.1:8000/v1/tools/search-indicators" `
    -ContentType "application/json" `
    -Body '{"query":"инфляция","categories":["economy"],"limit":10}'
```

Поиск выполняется только среди метрик, доступных в активном analytical run.

### Временной ряд

```json
{
  "countries": ["DEU", "FRA", "POL"],
  "metrics": ["2:NY.GDP.PCAP.CD"],
  "start_year": 2015,
  "end_year": 2024
}
```

### Snapshot за последний общий год

```json
{
  "countries": ["DEU", "FRA", "POL"],
  "metrics": ["gdp_per_capita", "unemployment_pct_labor_force"],
  "mode": "common_year"
}
```

Поддерживаемые режимы:

```text
latest_available
common_year
year
```

### Тренд

```json
{
  "country": "DEU",
  "metric": "gdp_per_capita",
  "start_year": 2015,
  "end_year": 2024
}
```

### Сравнение стран

```json
{
  "countries": ["DEU", "FRA", "POL"],
  "metric": "gdp_per_capita",
  "descending": true
}
```

Если `year` отсутствует, используется последний общий год с непустыми значениями для всех стран.

### Корреляция

```json
{
  "countries": ["DEU", "FRA", "POL"],
  "x_metric": "gdp_per_capita",
  "y_metric": "unemployment_pct_labor_force",
  "start_year": 2015,
  "end_year": 2024,
  "method": "pearson",
  "min_observations": 20
}
```

Ответ содержит `run_id`, source-qualified идентификаторы обеих метрик, units, dimension slices, `sample_size`, отброшенные пары и предупреждение, что корреляция не доказывает причинность.

## Response envelope

Успех:

```json
{
  "request_id": "9f9513b1-d507-43d0-9626-98aeb9bed792",
  "tool": "get_timeseries",
  "elapsed_ms": 18.347,
  "data": {
    "run_id": "20260815T124735Z_51798c06",
    "points": [],
    "warnings": []
  }
}
```

Заголовки ответа:

```text
X-Request-ID
X-Process-Time-Ms
```

Валидный входящий UUID в `X-Request-ID` сохраняется; иначе API генерирует новый.

Ошибка:

```json
{
  "error": {
    "code": "metric_not_found",
    "message": "Metric selector is absent from the active run.",
    "request_id": "9f9513b1-d507-43d0-9626-98aeb9bed792",
    "details": {}
  }
}
```

## Error codes

| HTTP | Code | Причина |
|---:|---|---|
| 404 | `metric_not_found` | Метрика отсутствует в active run |
| 404 | `country_not_found` | Страна отсутствует в active run |
| 404 | `no_active_run` | Нет загруженного run |
| 409 | `ambiguous_metric` | Код встречается в нескольких sources |
| 409 | `dimension_required` | Не выбран multidimensional slice |
| 422 | `validation_error` | Ошибка request schema |
| 422 | `invalid_request` | Некорректный аналитический запрос |
| 422 | `result_limit_exceeded` | Превышен safety limit |
| 503 | `analytics_unavailable` | ClickHouse или marts недоступны |
| 500 | `internal_error` | Непредвиденная ошибка |

Ответ `500` не содержит SQL, credentials, host ClickHouse или stack trace.

## Smoke-test

По умолчанию smoke-test запускает приложение in-process через FastAPI `TestClient` и использует настроенный ClickHouse:

```powershell
.\.venv\Scripts\python.exe scripts\smoke_test_api.py
```

Чтобы проверить уже запущенный HTTP-сервер, сначала запустите API, затем укажите URL:

```powershell
.\.venv\Scripts\python.exe -m wb_insight.api.main
```

Во втором терминале:

```powershell
$env:WB_TOOL_API_URL="http://127.0.0.1:8000"
.\.venv\Scripts\python.exe scripts\smoke_test_api.py
```

## Тестирование

Unit и contract tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not integration"
```

Live ClickHouse integration:

```powershell
$env:RUN_CLICKHOUSE_INTEGRATION="1"
$env:CLICKHOUSE_DATABASE="wb_insight_test"
.\.venv\Scripts\python.exe -m pytest `
    tests\integration\test_clickhouse_integration.py `
    -m integration `
    --no-cov `
    -vv
```

Integration test проверяет путь:

```text
synthetic Parquet
      ↓
ClickHouse loader + marts
      ↓
AnalyticalRepository
      ↓
ToolService
      ↓
FastAPI TestClient
      ↓
public JSON contract
```
