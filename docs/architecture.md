# Архитектура WB Insight Agent

## Цель документа

Документ фиксирует целевое направление архитектуры. В первом коммите реализован только базовый слой конфигурации и контроля качества репозитория.

## Архитектурные слои

```text
1. Ingestion
   World Bank API, пагинация, retries, сохранение исходных ответов

2. Storage and transformation
   Bronze/Silver/Gold, нормализация, проверки качества, ClickHouse

3. Analytics
   динамика, сравнение стран, peer benchmark, корреляции

4. Agent tools
   типизированные read-only функции для получения аналитических результатов

5. Agent and API
   оркестрация вызовов инструментов, verifier, FastAPI, пользовательский интерфейс
```

## Правило зависимостей

Зависимости должны идти только в сторону более низкого слоя:

```text
agent → tools → analytics → storage/transforms → ingestion
```

- `ingestion` ничего не знает об агенте;
- `analytics` не зависит от конкретной LLM;
- агент не получает произвольный SQL-доступ;
- ноутбуки используют функции пакета, но не содержат основной ETL-код;
- конфигурация стран и индикаторов хранится в YAML.

## Первый реализованный модуль

В первом коммите пакет `wb_insight` предоставляет:

- чтение переменных окружения;
- валидацию YAML-конфигурации через Pydantic;
- проверку уникальности индикаторов и ISO3-кодов;
- проверку соответствия целевого показателя реестру;
- CLI-команды `doctor` и `show-config`.

## Следующий вертикальный срез

```text
World Bank API
      ↓
WorldBankClient
      ↓
RawStore
      ↓
normalize_observations
      ↓
quality checks
      ↓
Parquet
```

Для этого будут добавлены каталоги:

```text
src/wb_insight/ingestion/
src/wb_insight/storage/
src/wb_insight/transforms/
src/wb_insight/quality/
tests/fixtures/
```

## Будущая привязка к Yandex Cloud

```text
Airflow DAG                 → Managed Service for Apache Airflow
RawStore                    → Yandex Object Storage
ClickHouse repository       → Managed Service for ClickHouse
Analytical marts            → Yandex DataLens
Agent backend               → Serverless Containers и API Gateway
LLM orchestration           → Yandex Cloud AI Studio
Secrets                     → Yandex Lockbox
```

Облачные компоненты не должны проникать в предметную аналитику напрямую. Интеграции будут скрыты за интерфейсами хранения и инструментов.
