# WB Insight Agent

Агентская аналитическая система для получения, подготовки и исследования макроэкономических и социальных показателей Всемирного банка.

Пользователь формулирует вопрос на естественном языке, а система должна подобрать показатели, обратиться к аналитическим витринам, выполнить расчеты и вернуть проверяемые выводы со ссылкой на дашборд. На текущем этапе реализуется надежный data layer, на который позднее будут опираться DataLens и ИИ-агент.

## Статус проекта

Текущая версия содержит первый рабочий локальный ETL-срез:

```text
World Bank API
      ↓
WorldBankClient
      ↓
append-only raw JSON
      ↓
нормализация pandas
      ↓
проверки качества
      ↓
Parquet + quality_report.json
```

Уже реализованы:

- типизированная конфигурация исследования;
- реестр стран, групп и семантики индикаторов (alias, category, unit);
- клиент World Bank Indicators API v2;
- автоматическая пагинация и retries;
- локальное append-only хранение raw-данных;
- нормализация стран, индикаторов и наблюдений;
- семантическое обогащение индикаторов единицами и категориями;
- загрузка произвольных индикаторов из одного или нескольких World Bank sources;
- контроль ключей, scope, периода, пропусков и покрытия;
- сохранение обработанных данных в Parquet;
- CLI-команда `ingest`;
- unit/integration tests без зависимости CI от внешнего API.

ClickHouse, Airflow, DataLens, аналитические витрины и агент будут добавляться следующими этапами.

## Исследовательская задача

Основная исследовательская гипотеза:

> Существует ли связь между уровнем экономического развития страны и выбранными социальными, инфраструктурными и экологическими показателями, и как эта связь меняется во времени для разных групп стран?

Период, пилотные страны и индикаторы задаются в `configs/` и не зашиваются в Python-код.

## Требования

- Python 3.12;
- Git;
- доступ к интернету только для реального запуска ingestion;
- PowerShell, CMD, Git Bash, WSL или Unix-подобный терминал.

## Быстрый запуск

### Windows PowerShell

```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
python -m wb_insight doctor
```

Если активировать окружение неудобно, все команды можно запускать напрямую через его интерпретатор:

```powershell
.\.venv\Scripts\python.exe -m wb_insight doctor
.\.venv\Scripts\python.exe -m pytest
```

### Linux, macOS, WSL или Git Bash

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
cp .env.example .env
python -m wb_insight doctor
```

## Конфигурация

Основные файлы:

- `configs/research.yaml` — период, исследовательские вопросы и пилотные страны;
- `configs/indicators.yaml` — семантический реестр, default-набор показателей и default `source_id` World Bank;
- `configs/country_groups.yaml` — пользовательские группы стран;
- `.env` — локальные настройки, не добавляемые в Git;
- `.env.example` — безопасный шаблон окружения.

## World Bank API client

```python
from wb_insight.ingestion import WorldBankClient

with WorldBankClient() as client:
    countries = client.get_countries()
    indicators = client.get_indicators(source_id=2)
    observations = client.get_observations(
        country_codes=["DEU", "NLD"],
        indicator_codes=["NY.GDP.PCAP.CD", "SP.POP.TOTL"],
        start_year=2020,
        end_year=2024,
        source_id=2,
    )
```

Клиент возвращает сырые JSON-записи. Их подготовка выполняется отдельным transformation layer.

## Запуск ETL

Запуск для scope из конфигурации:

```powershell
.\.venv\Scripts\python.exe -m wb_insight ingest
```

Можно переопределить scope без изменения YAML:

```powershell
.\.venv\Scripts\python.exe -m wb_insight ingest `
  --countries DEU,NLD `
  --indicators gdp_per_capita,population `
  --start-year 2020 `
  --end-year 2024
```

`--indicators` принимает три вида селекторов:

1. alias из `configs/indicators.yaml`, например `gdp_per_capita`;
2. обычный код World Bank, например `SL.UEM.TOTL.ZS`;
3. явно квалифицированный код `SOURCE_ID:INDICATOR_CODE`, например `6:DT.DOD.DECT.CD`.

Поле `enabled` управляет только набором по умолчанию, когда `--indicators` не указан.
Незарегистрированный код без `SOURCE_ID:` интерпретируется в default source из
`configs/indicators.yaml` (сейчас `source_id: 2`, WDI). Для показателя из другой базы
используйте явный селектор `SOURCE_ID:CODE`. Это устраняет дорогой и ненадежный поиск
по глобальному каталогу всех источников.

Sources можно смешивать в одном запуске:

```powershell
.\.venv\Scripts\python.exe -m wb_insight ingest `
  --countries ARG,BRA,IND `
  --indicators 2:NY.GDP.PCAP.CD,6:DT.DOD.DECT.CD `
  --start-year 2015 `
  --end-year 2023
```

Pipeline сначала читает concepts выбранного source. Источники, у которых данные имеют
только измерения `Country`, `Series`, `Time`, загружаются через классический Indicators API.
Если source содержит дополнительные dimensions, pipeline автоматически переключается на
Advanced Data API и сохраняет эти измерения в `dimensions_json`.

Для IDS (`source 6`) дополнительное измерение `Counterpart-Area` по умолчанию выбирается
как `WLD` (World/total), если эта переменная доступна. Его можно переопределить:

```powershell
.\.venv\Scripts\python.exe -m wb_insight ingest `
  --countries ARG,BRA,IND `
  --indicators 6:DT.DOD.DECT.CD `
  --dimensions 6:Counterpart-Area=001 `
  --start-year 2015 `
  --end-year 2023
```

Для других multidimensional sources дополнительный фильтр задается в формате
`SOURCE_ID:CONCEPT=VALUE`. Несколько значений одного измерения разделяются `;`, а разные
измерения — запятой, например:

```text
--dimensions 6:Counterpart-Area=WLD;001,57:Version=199704
```

Во время запуска pipeline:

1. получает глобальный справочник стран;
2. разрешает alias/raw/source-qualified selectors в пары `(source_id, indicator_code)`;
3. отдельно получает concepts и `indicator?source=...` catalog для каждого выбранного source;
4. проверяет страны и indicator codes внутри соответствующего source;
5. выбирает classic или advanced adapter по размерности базы;
6. получает observations, сохраняя дополнительные dimensions;
7. сохраняет raw metadata и observations раздельно по source;
8. нормализует данные и сохраняет `source_id` как часть ключа индикатора;
9. выполняет блокирующие проверки и предупреждения;
10. сохраняет `countries.parquet`, `indicators.parquet`, `observations.parquet` и `quality_report.json`.

Для classic sources намеренно используется один индикатор на HTTP-запрос. Это немного
увеличивает число запросов, но позволяет точно определить ряд, вызвавший ошибку API.

## Семантический реестр единиц и категорий

`configs/indicators.yaml` больше не является allowlist. Он выполняет две функции:

1. задает default-набор индикаторов (`enabled: true`);
2. хранит проверенную семантику для известных показателей.

Верхнеуровневый `source_id` является default source. Конкретная запись может переопределить его:

```yaml
source_id: 2

indicators:
  - code: NY.GDP.PCAP.CD
    alias: gdp_per_capita
    name_ru: ВВП на душу населения, текущие доллары США
    category: economy
    role: target
    enabled: true
    unit: current_usd_per_person
    display_unit: US$ / person

  - code: DT.DOD.DECT.CD
    source_id: 6
    alias: external_debt
    name_ru: Внешний долг
    category: debt
    role: feature
    enabled: false
```

Для зарегистрированного индикатора processed-слой получает стабильные `alias`, `category`, `unit` и `display_unit`. Для незарегистрированного индикатора pipeline:

- сохраняет его без ошибки, если пара `(source_id, code)` существует в World Bank;
- берет категорию из World Bank topics, если они доступны;
- использует `unit` из API только если World Bank действительно его возвращает;
- **не угадывает единицу по названию**;
- оставляет неизвестную единицу `null` и пишет warning в `quality_report.json`.

В `indicators.parquet` сохраняются поля `source_unit`, `unit`, `display_unit`, `unit_source`, `category`, `category_source`, `is_registered`. Эти же семантические атрибуты переносятся в `observations.parquet`, поэтому аналитика и будущий агент могут отличать проверенную единицу из registry от метаданных World Bank и от отсутствующей семантики.

## Структура данных

Raw-слой не перезаписывает предыдущие запуски:

```text
data/raw/
├── countries/
│   └── load_date=2026-08-15/
│       └── run_id=.../
│           └── countries.json
├── source_2_concepts/
├── indicators_source_2/
├── source_6_concepts/
├── indicators_source_6/
├── observations_source_2/
└── observations_source_6/
```

Processed-слой:

```text
data/processed/
└── run_id=.../
    ├── countries.parquet
    ├── indicators.parquet
    ├── observations.parquet
    └── quality_report.json
```

В Git эти результаты не добавляются; директории сохраняются через `.gitkeep`.

## Проверки качества

На текущем этапе проверяются:

- наличие обязательных колонок;
- непустые датасеты;
- уникальность ключей;
- наличие выбранных стран в справочнике;
- наличие выбранных `(source_id, indicator_code)` в indicator catalog соответствующего source;
- покрытие единиц измерения для выбранных индикаторов — warning, если unit неизвестен;
- покрытие категорий для выбранных индикаторов — warning, если category не определена;
- отсутствие посторонних стран и source-qualified индикаторов в observations;
- корректность диапазона лет;
- отсутствие агрегатов после фильтрации;
- пропущенные значения метрик — как warning, а не как нули;
- покрытие базовой сетки `source × country × indicator × year` — как warning;
- дополнительные dimensions участвуют в уникальном ключе observations и не теряются.

Критические ошибки блокируют создание Parquet, но raw-артефакты и `quality_report.json` остаются для диагностики.

## Проверка качества кода

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy src
.\.venv\Scripts\python.exe -m pytest
```

При наличии `make`:

```bash
make check
```

## Ключевые каталоги

```text
src/wb_insight/
├── ingestion/       # World Bank HTTP client
├── storage/         # raw storage
├── transforms/      # JSON -> DataFrame
├── quality/         # data-quality rules
├── pipeline.py      # orchestration of the local ETL
├── cli.py
└── config.py

tests/
├── fixtures/
├── test_world_bank_client.py
├── test_raw_store.py
├── test_transforms.py
├── test_quality.py
└── test_pipeline.py
```

## Следующий этап

После стабилизации локального ETL планируется:

```text
Parquet / raw JSON
      ↓
Yandex Object Storage + ClickHouse
      ↓
аналитические витрины
      ↓
Yandex DataLens
      ↓
read-only tools
      ↓
Yandex Cloud AI Studio agent
```

Бизнес-логика трансформаций и проверок должна остаться независимой от конкретной облачной инфраструктуры.

## Git

- `.venv`, `.env`, кэши и реальные данные не коммитятся;
- `tests/fixtures/` коммитятся, так как используются для изолированных тестов;
- перед коммитом рекомендуется запускать Ruff, mypy и pytest;
- крупные функциональные этапы оформляются отдельными коммитами или pull request.

## Универсальные витрины для DataLens

После любого успешного `ingest` processed-run можно преобразовать в CSV без изменения Python-кода:

```powershell
.\.venv\Scripts\python.exe scripts\export_datalens_csv.py `
  --run-dir "data\processed\run_id=<RUN_ID>" `
  --output-dir "data\marts\run_id=<RUN_ID>"
```

Создаются четыре артефакта:

```text
data/marts/run_id=<RUN_ID>/
├── worldbank_datalens_wide.csv
├── worldbank_datalens_long.csv
├── worldbank_metric_catalog.csv
└── mart_manifest.json
```

Builder не содержит списка конкретных стран или индикаторов:

- зарегистрированный indicator использует `alias` из semantic registry;
- незарегистрированный indicator получает стабильное техническое имя `s<SOURCE>_<CODE>`;
- один и тот же code из разных sources не смешивается;
- дополнительные source dimensions сохраняются в long mart и превращаются в отдельные wide-колонки;
- никакая многомерная серия не агрегируется молча;
- список стран и период определяются содержимым конкретного ingestion-run.

Можно дополнительно фильтровать run:

```powershell
.\.venv\Scripts\python.exe scripts\export_datalens_csv.py `
  --run-dir "data\processed\run_id=<RUN_ID>" `
  --output-dir "data\marts\subset" `
  --countries DEU,NLD,POL `
  --indicators gdp_per_capita,SL.UEM.TOTL.ZS,6:DT.DOD.DECT.CD `
  --start-year 2015 `
  --end-year 2024
```

Для бизнес-названий, локализованных названий стран и производных метрик можно передать
необязательный config:

```powershell
.\.venv\Scripts\python.exe scripts\export_datalens_csv.py `
  --run-dir "data\processed\run_id=<RUN_ID>" `
  --output-dir "data\marts\run_id=<RUN_ID>" `
  --config configs\marts.example.yaml
```

`configs/marts.example.yaml` показывает поддерживаемые поля `column_aliases`,
`country_labels` и `derived_metrics`. Формулы derived metrics ограничены безопасной
арифметикой над уже построенными wide-колонками и не используют `eval`.

По умолчанию дополнительные dimensions кодируются в именах wide-колонок. Чтобы вместо
этого блокировать multidimensional runs:

```text
--dimension-mode error
```

CSV сохраняются в `utf-8-sig`, а `data/marts/*` исключен из Git так же, как raw/processed данные.
