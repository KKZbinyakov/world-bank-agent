# WB Insight Agent

Agentic-аналитическая система для получения, нормализации и исследования макроэкономических и социальных показателей Всемирного банка.

Проект строится как воспроизводимый data pipeline:

```text
World Bank API
      ↓
source-aware ingestion
      ↓
append-only raw JSON
      ↓
нормализация + semantic enrichment
      ↓
quality checks
      ↓
Silver: Parquet
      ↓
универсальный mart builder
      ↓
Gold: CSV для DataLens
      ↓
Yandex DataLens
      ↓
read-only analytical tools
      ↓
Yandex Cloud AI Studio agent
```

Сейчас реализованы ingestion, Silver-слой, semantic registry, универсальные DataLens-витрины и ClickHouse storage/loader. Airflow и LLM-агент будут добавлены следующими этапами.

---

## Что уже реализовано

- типизированная конфигурация проекта;
- клиент World Bank Indicators API v2;
- пагинация и обработка ошибок API;
- поддержка нескольких World Bank sources в одном запуске;
- автоматическое определение структуры source через `concepts`;
- classic Indicators API для обычных источников;
- Advanced Data API для многомерных источников;
- дополнительные dimensions без потери данных;
- append-only raw storage;
- нормализация стран, индикаторов и наблюдений;
- semantic registry: alias, категория, единица измерения, display unit;
- поддержка произвольных World Bank indicators, даже если они отсутствуют в registry;
- data-quality checks;
- Silver-слой в Parquet;
- универсальный mart builder;
- wide и long CSV для DataLens;
- каталог метрик и manifest;
- presentation-конфиг `configs/marts.yaml`;
- производные метрики;
- unit/integration tests, Ruff и mypy;
- локальный ClickHouse через Docker Compose;
- загрузка Silver Parquet и Gold marts в ClickHouse;
- SQL views для временных рядов, snapshot и data quality.

---

## Требования

- Python 3.12;
- Git;
- доступ к интернету для реального ingestion;
- PowerShell, CMD, Git Bash, WSL, Linux или macOS.

---

# 1. Установка

## Windows PowerShell

Создать виртуальное окружение:

```powershell
python -m venv .venv
```

Если PowerShell разрешает активацию:

```powershell
.\.venv\Scripts\Activate.ps1
```

Если выполнение `.ps1` запрещено, можно либо разрешить его только для текущего процесса:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

либо вообще не активировать окружение и всегда использовать:

```powershell
.\.venv\Scripts\python.exe
```

Установить зависимости:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Создать локальный `.env`:

```powershell
Copy-Item .env.example .env
```

Проверить проект:

```powershell
.\.venv\Scripts\python.exe -m wb_insight doctor
```

## Linux / macOS / WSL

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
cp .env.example .env
python -m wb_insight doctor
```

---

# 2. Конфигурация

Основные файлы:

```text
configs/
├── research.yaml
├── indicators.yaml
├── country_groups.yaml
└── marts.yaml
```

### `configs/research.yaml`

Содержит:

- период исследования по умолчанию;
- пилотные страны;
- исследовательские вопросы;
- правила обработки данных.

### `configs/indicators.yaml`

Semantic registry индикаторов.

Registry **не является allowlist**. Неизвестный индикатор можно передать явно, и pipeline загрузит его, если он существует в соответствующем World Bank source.

Пример:

```yaml
source_id: 2

indicators:
  - code: NY.GDP.PCAP.CD
    alias: gdp_per_capita
    name_ru: "ВВП на душу населения, текущие доллары США"
    category: economy
    role: target
    enabled: true
    unit: current_usd_per_person
    display_unit: "US$ / person"

  - code: GOV_WGI_CC.EST
    source_id: 3
    alias: control_of_corruption_estimate
    name_ru: "Контроль коррупции"
    category: governance
    role: feature
    enabled: false
    unit: governance_estimate
    display_unit: estimate
```

`enabled: true` означает только: использовать показатель в default-ingestion, если `--indicators` не указан.

### `configs/marts.yaml`

Presentation-конфиг Gold-витрины.

В текущем основном датасете он содержит:

- 50 стабильных business aliases;
- 50 русских названий стран;
- `country_ru`;
- производную метрику `employed_derived_total`.

Пример:

```yaml
column_aliases:
  "2:NY.GDP.MKTP.CD": gdp_current_usd
  "2:NY.GDP.PCAP.CD": gdp_per_capita_current_usd
  "3:GOV_WGI_CC.EST": control_of_corruption_estimate
  "63:HD.HCI.OVRL": hci_proxy_0_1_not_hdi

country_label_column: country_ru

derived_metrics:
  - name: employed_derived_total
    expression: "labor_force_total * (1 - unemployment_pct_labor_force / 100)"
    round: 0
```

---

# 3. World Bank API client

Клиент можно использовать отдельно от ETL:

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

---

# 4. Ingestion

## Default scope

```powershell
.\.venv\Scripts\python.exe -m wb_insight ingest
```

## Произвольные страны, индикаторы и период

```powershell
.\.venv\Scripts\python.exe -m wb_insight ingest `
    --countries DEU,NLD,POL `
    --indicators NY.GDP.PCAP.CD,SL.UEM.TOTL.ZS `
    --start-year 2015 `
    --end-year 2024
```

`--indicators` поддерживает три формата:

```text
gdp_per_capita
NY.GDP.PCAP.CD
2:NY.GDP.PCAP.CD
```

То есть:

1. alias из `configs/indicators.yaml`;
2. World Bank indicator code;
3. точная пара `SOURCE_ID:CODE`.

Если raw-код передан без `SOURCE_ID`, используется default source из `configs/indicators.yaml`.

---

# 5. Multi-source ingestion

В одном run можно смешивать несколько баз World Bank.

Пример WDI + IDS:

```powershell
.\.venv\Scripts\python.exe -m wb_insight ingest `
    --countries ARG,BRA,IND `
    --indicators 2:NY.GDP.PCAP.CD,6:DT.DOD.DECT.CD `
    --start-year 2015 `
    --end-year 2023
```

Pipeline для каждого source отдельно:

1. получает `concepts`;
2. получает source-specific indicator catalog;
3. проверяет доступность стран;
4. валидирует `(source_id, indicator_code)`;
5. определяет размерность базы;
6. выбирает classic или advanced adapter;
7. загружает observations.

Для обычного источника с dimensions:

```text
Country
Series
Time
```

используется classic Indicators API.

Для многомерного source используется Advanced Data API.

---

# 6. Дополнительные dimensions

Например, IDS (`source 6`) содержит `Counterpart-Area`.

По умолчанию pipeline использует `WLD`, если это измерение доступно.

Явное значение:

```powershell
.\.venv\Scripts\python.exe -m wb_insight ingest `
    --countries ARG,BRA,IND `
    --indicators 6:DT.DOD.DECT.CD `
    --dimensions 6:Counterpart-Area=001 `
    --start-year 2015 `
    --end-year 2023
```

Несколько значений:

```text
--dimensions 6:Counterpart-Area=WLD;001
```

Несколько source/dimension filters:

```text
--dimensions 6:Counterpart-Area=WLD;001,57:Version=199704
```

Дополнительные dimensions сохраняются в `dimensions_json` и участвуют в уникальном ключе observation.

---

# 7. Что создаёт ingestion

Raw-слой:

```text
data/raw/
├── countries/
├── source_2_concepts/
├── source_3_concepts/
├── source_63_concepts/
├── indicators_source_2/
├── indicators_source_3/
├── indicators_source_63/
├── observations_source_2/
├── observations_source_3/
└── observations_source_63/
```

Конкретный набор папок зависит от sources текущего run.

Silver-слой:

```text
data/processed/
└── run_id=.../
    ├── countries.parquet
    ├── indicators.parquet
    ├── observations.parquet
    └── quality_report.json
```

Данные в `data/raw`, `data/processed` и `data/marts` не коммитятся в Git.

---

# 8. Data quality

Pipeline проверяет:

- непустые датасеты;
- обязательные колонки;
- уникальность ключей;
- наличие выбранных стран;
- отсутствие агрегированных сущностей;
- наличие source/indicator pairs;
- диапазон лет;
- отсутствие посторонних стран;
- отсутствие посторонних indicators;
- пропущенные значения;
- покрытие сетки country × indicator × year;
- unit metadata;
- category metadata;
- уникальность с учётом дополнительных dimensions.

Пропуск значения **не заменяется нулём**.

Warning не блокирует run. Failure блокирует processed-слой.

Посмотреть отчёт:

```powershell
Get-Content "data\processed\run_id=<RUN_ID>\quality_report.json" |
    ConvertFrom-Json |
    ConvertTo-Json -Depth 10
```

---

# 9. Основной датасет проекта

Текущий основной аналитический scope:

- 50 стран;
- 2000–2024;
- 50 исходных World Bank indicators;
- World Bank sources:
  - `2` — WDI;
  - `3` — WGI;
  - `63` — Human Capital Index;
- 1 производная метрика `employed_derived_total`.

## Страны

```powershell
$countries = @(
    "RUS","KAZ","CHN","IND","TUR","DEU","USA","BRA","ZAF","JPN",
    "GBR","FRA","ITA","ESP","POL","SWE","UKR","GEO","KOR","IDN",
    "MYS","THA","VNM","PHL","AUS","BGD","LKA","NPL","MEX","ARG",
    "CHL","COL","PER","CRI","NGA","KEN","GHA","ETH","TZA","SEN",
    "RWA","UGA","MWI","PAK","SAU","ARE","ISR","EGY","MAR","CAN"
) -join ","
```

## Индикаторы

```powershell
$indicators = @(
    "2:NY.GDP.MKTP.CD",
    "2:NY.GDP.PCAP.CD",
    "2:NY.GDP.MKTP.KD.ZG",
    "2:NY.GDP.PCAP.KD",
    "2:FP.CPI.TOTL.ZG",
    "2:PA.NUS.FCRF",

    "2:SL.UEM.TOTL.ZS",
    "2:SL.TLF.TOTL.IN",
    "2:SL.TLF.CACT.ZS",
    "2:SL.EMP.TOTL.SP.ZS",
    "2:SL.UEM.1524.ZS",

    "2:SP.POP.TOTL",
    "2:SP.POP.GROW",
    "2:SP.POP.65UP.TO.ZS",
    "2:SP.URB.TOTL.IN.ZS",
    "2:SP.DYN.TFRT.IN",
    "2:SP.DYN.LE00.IN",

    "63:HD.HCI.OVRL",

    "2:SI.POV.NAHC",
    "2:SI.POV.GINI",
    "2:SI.POV.DDAY",

    "2:GC.XPN.TOTL.GD.ZS",
    "2:GC.DOD.TOTL.GD.ZS",
    "2:NE.CON.GOVT.ZS",
    "2:FI.RES.TOTL.CD",
    "2:FS.AST.PRVT.GD.ZS",
    "2:FM.LBL.BMNY.GD.ZS",

    "2:NE.EXP.GNFS.CD",
    "2:NE.IMP.GNFS.CD",
    "2:NE.TRD.GNFS.ZS",
    "2:BN.CAB.XOKA.GD.ZS",
    "2:BX.KLT.DINV.WD.GD.ZS",
    "2:NE.GDI.TOTL.ZS",

    "2:FB.ATM.TOTL.P5",
    "2:IT.NET.USER.ZS",
    "2:EG.ELC.ACCS.ZS",

    "2:SH.XPD.CHEX.GD.ZS",
    "2:SE.TER.ENRR",
    "2:GB.XPD.RSDV.GD.ZS",

    "2:EG.FEC.RNEW.ZS",
    "2:EN.GHG.CO2.PC.CE.AR5",

    "2:NV.AGR.TOTL.ZS",
    "2:NV.IND.TOTL.ZS",
    "2:NV.SRV.TOTL.ZS",

    "3:GOV_WGI_CC.EST",
    "3:GOV_WGI_GE.EST",
    "3:GOV_WGI_PV.EST",
    "3:GOV_WGI_RQ.EST",
    "3:GOV_WGI_RL.EST",
    "3:GOV_WGI_VA.EST"
) -join ","
```

Проверка:

```powershell
($countries -split ",").Count
($indicators -split ",").Count
```

Ожидается:

```text
50
50
```

## Запуск основной выгрузки

```powershell
.\.venv\Scripts\python.exe -m wb_insight ingest `
    --countries $countries `
    --indicators $indicators `
    --start-year 2000 `
    --end-year 2024
```

---

# 10. Поиск последнего processed run

```powershell
Get-ChildItem data\processed -Directory |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 5 Name, LastWriteTime
```

---

# 11. Универсальный mart builder

Builder принимает любой корректный processed-run:

```text
countries.parquet
indicators.parquet
observations.parquet
```

и не зависит от конкретного списка стран или индикаторов.

Без presentation-конфига:

```powershell
.\.venv\Scripts\python.exe scripts\export_datalens_csv.py `
    --run-dir "data\processed\run_id=<RUN_ID>" `
    --output-dir "data\marts\run_id=<RUN_ID>"
```

Для основного проекта рекомендуется использовать `configs/marts.yaml`:

```powershell
.\.venv\Scripts\python.exe scripts\export_datalens_csv.py `
    --run-dir "data\processed\run_id=<RUN_ID>" `
    --output-dir "data\marts\configured" `
    --config "configs\marts.yaml"
```

Builder создаёт:

```text
data/marts/configured/
├── worldbank_datalens_wide.csv
├── worldbank_datalens_long.csv
├── worldbank_metric_catalog.csv
└── mart_manifest.json
```

### `worldbank_datalens_wide.csv`

Основной файл для DataLens.

Одна строка:

```text
country × year
```

Колонки:

- страна;
- регион;
- income group;
- координаты;
- год;
- показатели;
- derived metrics.

### `worldbank_datalens_long.csv`

Универсальная long-витрина:

```text
country
year
source
indicator
value
unit
category
dimensions
```

Подходит для metric explorer и будущих analytical tools.

### `worldbank_metric_catalog.csv`

Связывает:

```text
source_id
indicator_code
indicator_name
alias
wide_column
category
unit
display_unit
dimensions
```

### `mart_manifest.json`

Содержит:

- число строк;
- число стран;
- диапазон лет;
- число метрик;
- missing ratio;
- presentation aliases;
- country labels;
- derived metrics.

---

# 12. Контрольная проверка основной витрины

После сборки с `configs/marts.yaml`:

```powershell
Get-Content "data\marts\configured\mart_manifest.json" |
    ConvertFrom-Json |
    ConvertTo-Json -Depth 10
```

Для текущего основного scope ожидается:

```text
wide rows: 1250
countries: 50
years: 2000–2024
source_indicator_pairs: 50
complete_grid: true
configured_column_aliases: 50
configured_country_labels: 50
derived_metrics:
  - employed_derived_total
```

Проверка wide CSV:

```powershell
.\.venv\Scripts\python.exe -c "import pandas as pd; d=pd.read_csv(r'data\marts\configured\worldbank_datalens_wide.csv'); print('shape:',d.shape); print('countries:',d['country_code'].nunique()); print('years:',d['year'].min(),'-',d['year'].max()); print('duplicates:',d.duplicated(['country_code','year']).sum()); print('country_ru:', 'country_ru' in d.columns); print('derived:', 'employed_derived_total' in d.columns); print('technical s2 columns:',sum(c.startswith('s2_') for c in d.columns))"
```

Ожидается:

```text
countries: 50
years: 2000 - 2024
duplicates: 0
country_ru: True
derived: True
technical s2 columns: 0
```

Проверка derived metric:

```powershell
.\.venv\Scripts\python.exe -c "import pandas as pd, numpy as np; d=pd.read_csv(r'data\marts\configured\worldbank_datalens_wide.csv'); expected=(d['labor_force_total']*(1-d['unemployment_pct_labor_force']/100)).round(); mask=expected.notna() & d['employed_derived_total'].notna(); print('rows checked:',mask.sum()); print('mismatches:',(~np.isclose(d.loc[mask,'employed_derived_total'],expected[mask])).sum())"
```

Ожидается:

```text
mismatches: 0
```

---

# 13. Загрузка в Yandex DataLens

Для основного дашборда используйте:

```text
worldbank_datalens_wide.csv
```

Рекомендуемые поля:

- Dimension:
  - `country_ru`;
  - `country_name`;
  - `country_code`;
  - `region_name`;
  - `income_level_name`;
  - `year`.

- Geo:
  - `latitude`;
  - `longitude`.

- Measures:
  - все metric columns.

Первый smoke-test DataLens:

1. Line chart:
   - X: `year`;
   - Y: `gdp_per_capita_current_usd`;
   - Color: `country_ru`.

2. Scatter:
   - X: `gdp_per_capita_current_usd`;
   - Y: `life_expectancy_years`;
   - Color: `region_name`.

3. Map:
   - latitude / longitude;
   - показатель: `gdp_per_capita_current_usd`.

4. Dashboard filters:
   - `country_ru`;
   - `region_name`;
   - `income_level_name`;
   - `year`.

Пропущенные World Bank значения должны оставаться пустыми, а не заменяться нулями.

---

# 14. ClickHouse analytical storage

## Локальный ClickHouse

Для разработки используется Docker Compose с ClickHouse 26.7.1.

Убедитесь, что в `.env` заданы параметры, соответствующие локальному compose:

```dotenv
CLICKHOUSE_HOST=localhost
CLICKHOUSE_PORT=8123
CLICKHOUSE_DATABASE=wb_insight
CLICKHOUSE_USER=wb_insight
CLICKHOUSE_PASSWORD=wb_insight_local
CLICKHOUSE_SECURE=false
```

Запустить ClickHouse:

```powershell
docker compose up -d clickhouse
```

Проверить состояние:

```powershell
docker compose ps
```

Посмотреть логи при необходимости:

```powershell
docker compose logs -f clickhouse
```

Остановить контейнер без удаления данных:

```powershell
docker compose stop clickhouse
```

Удалить контейнер и локальные ClickHouse volumes:

```powershell
docker compose down -v
```

## Загрузка processed run в ClickHouse

Сначала убедитесь, что Gold CSV уже собраны для того же run:

```powershell
.\.venv\Scripts\python.exe scripts\export_datalens_csv.py `
    --run-dir "data\processed\run_id=<RUN_ID>" `
    --output-dir "data\marts\run_id=<RUN_ID>" `
    --config "configs\marts.yaml"
```

Затем загрузите Silver + Gold:

```powershell
.\.venv\Scripts\python.exe scripts\load_clickhouse.py `
    --run-dir "data\processed\run_id=<RUN_ID>" `
    --mart-dir "data\marts\run_id=<RUN_ID>"
```

Loader выполняет:

1. проверку подключения;
2. применение `sql/ddl/*.sql`;
3. идемпотентную загрузку `dim_country`, `dim_indicator`, `fact_observation`;
4. загрузку текущего wide mart в `mart_country_year_wide`;
5. загрузку `mart_metric_catalog`;
6. регистрацию run в `etl_run`;
7. применение `sql/marts/*.sql`.

Повторная загрузка того же `run_id` не создает дублей: строки конкретного run
удаляются синхронно и загружаются заново.

## Проверка ClickHouse

```powershell
.\.venv\Scripts\python.exe scripts\smoke_test_clickhouse.py
```

Ожидаются ненулевые значения для `runs`, `countries`, `indicators` и `observations`.
Для основной витрины проекта `wide_rows` должен быть 1250.

Основные таблицы:

```text
etl_run
dim_country
dim_indicator
fact_observation
mart_country_year_wide
mart_metric_catalog
```

Основные SQL views:

```text
mart_indicator_timeseries
mart_country_snapshot
mart_data_quality
mart_country_year
```

## Подключение DataLens к ClickHouse

После локальной проверки тот же loader используется с Yandex Managed Service for
ClickHouse. Для managed-кластера укажите в `.env` FQDN, пользователя, пароль,
HTTPS-порт и включите TLS, например:

```dotenv
CLICKHOUSE_HOST=<clickhouse-host-fqdn>
CLICKHOUSE_PORT=8443
CLICKHOUSE_DATABASE=wb_insight
CLICKHOUSE_USER=<loader-user>
CLICKHOUSE_PASSWORD=<secret>
CLICKHOUSE_SECURE=true
```

Для DataLens создайте отдельного read-only пользователя. Для существующего wide
дашборда выберите таблицу:

```text
mart_country_year_wide
```

Для универсальных metric charts и будущих analytical tools используйте:

```text
mart_indicator_timeseries
mart_country_snapshot
mart_data_quality
```

---

# 15. Continuous Integration

Workflow:

```text
.github/workflows/ci.yml
```

запускается для каждого pull request, push в `main` и вручную через
`workflow_dispatch`.

В CI есть два обязательных job.

### `Quality`

Проверяет:

```text
Ruff format
Ruff lint
mypy
pytest + coverage >= 80%
сборку Python package
```

Integration-тесты исключаются из этого job, поэтому он не зависит от внешних
сервисов.

### `ClickHouse integration`

GitHub Actions поднимает отдельный контейнер ClickHouse и на синтетическом
fixture проверяет полный контур:

```text
DDL
  ↓
Silver Parquet fixture
  ↓
ClickHouse loader
  ↓
SQL views
  ↓
wide mart + metric catalog
  ↓
idempotent reload
```

Тест не обращается к World Bank API и не использует реальные проектные данные.

Локальный запуск только unit-тестов:

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not integration"
```

Локальный integration-тест необходимо выполнять только против отдельной базы
`wb_insight_test` или `wb_insight_ci`. Для уже запущенного Docker-контейнера сначала
создайте тестовую базу:

```powershell
docker exec -it wb-insight-clickhouse clickhouse-client `
    --user wb_insight `
    --password wb_insight_local `
    --query "CREATE DATABASE IF NOT EXISTS wb_insight_test"
```

Затем запустите тест:

```powershell
$env:RUN_CLICKHOUSE_INTEGRATION="1"
$env:CLICKHOUSE_DATABASE="wb_insight_test"
$env:CLICKHOUSE_HOST="localhost"
$env:CLICKHOUSE_PORT="8123"
$env:CLICKHOUSE_USER="wb_insight"
$env:CLICKHOUSE_PASSWORD="wb_insight_local"
$env:CLICKHOUSE_SECURE="false"

.\.venv\Scripts\python.exe -m pytest `
    tests\integration\test_clickhouse_integration.py `
    -m integration `
    --no-cov `
    -vv
```

Integration-тест намеренно отказывается работать с основной локальной базой
`wb_insight`, чтобы случайно не заменить её динамические Gold-таблицы.

После успешного PR рекомендуется включить для ветки `main` branch protection и
сделать обязательными проверки:

```text
Quality
ClickHouse integration
```

---

# 16. Проверка качества кода

Перед коммитом:

```powershell
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy src
.\.venv\Scripts\python.exe -m pytest
```

При наличии `make`:

```bash
make check
```

---

# 17. Git и `.gitignore`

В Git не должны попадать:

```text
.venv/
.env
__pycache__/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/
data/raw/*
data/processed/*
data/marts/*
```

В Git должны оставаться:

```text
.env.example
data/raw/.gitkeep
data/processed/.gitkeep
data/marts/.gitkeep
tests/fixtures/
configs/
src/
scripts/
README.md
```

Проверить:

```powershell
git status --short
```

Для конкретного файла:

```powershell
git check-ignore -v .env
git check-ignore -v .venv
git check-ignore -v "data\marts\configured\worldbank_datalens_wide.csv"
```

---

# 18. Troubleshooting

## `No module named ...`

Убедитесь, что используется Python из `.venv`:

```powershell
.\.venv\Scripts\python.exe -c "import sys; print(sys.executable)"
```

## PowerShell не позволяет активировать `.venv`

Не обязательно активировать окружение. Используйте:

```powershell
.\.venv\Scripts\python.exe
```

## `World Bank does not expose selected source/indicator pairs`

Проверьте:

- indicator code;
- `source_id`;
- используйте явный формат `SOURCE_ID:CODE`.

## Показатель отсутствует в `configs/indicators.yaml`

Это не ошибка. Registry не является allowlist.

Передайте indicator явно:

```text
2:SOME.CODE
```

Если indicator существует в source, ingestion продолжится.

## `Selected indicators missing reliable unit metadata`

Это warning.

Данные загружены, но для показателя нет надёжной единицы измерения. Для важных метрик добавьте `unit` и `display_unit` в `configs/indicators.yaml`.

## В `value` есть null

Это нормально для World Bank.

Некоторые показатели:

- публикуются не ежегодно;
- имеют ограниченное покрытие стран;
- появились позже начала периода.

Null нельзя автоматически заменять нулём.

## Long CSV выдаёт `DtypeWarning` в pandas

Long-витрина содержит metadata-поля разных типов. Для основного DataLens dashboard рекомендуется `worldbank_datalens_wide.csv`.

---

# 19. Структура проекта

```text
wb-insight-agent/
├── configs/
│   ├── research.yaml
│   ├── indicators.yaml
│   ├── country_groups.yaml
│   └── marts.yaml
│
├── src/
│   └── wb_insight/
│       ├── ingestion/
│       ├── storage/
│       ├── transforms/
│       ├── quality/
│       ├── marts/
│       ├── pipeline.py
│       ├── config.py
│       └── cli.py
│
├── scripts/
│   ├── export_datalens_csv.py
│   ├── load_clickhouse.py
│   └── smoke_test_clickhouse.py
│
├── sql/
│   ├── ddl/
│   └── marts/
│
├── data/
│   ├── raw/
│   ├── processed/
│   └── marts/
│
├── .github/
│   └── workflows/
│       └── ci.yml
│
├── tests/
│   ├── fixtures/
│   ├── integration/
│   │   └── test_clickhouse_integration.py
│   ├── test_world_bank_client.py
│   ├── test_raw_store.py
│   ├── test_transforms.py
│   ├── test_quality.py
│   ├── test_pipeline.py
│   ├── test_marts.py
│   └── test_clickhouse_storage.py
│
├── docs/
├── docker-compose.yml
├── README.md
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
└── .env.example
```

---

# 20. Следующие этапы

Текущий data layer:

```text
World Bank
    ↓
raw
    ↓
Silver Parquet
    ↓
Gold DataLens CSV
    ↓
ClickHouse tables + SQL marts
    ↓
DataLens
```

Следующие компоненты:

```text
Yandex Object Storage / Managed ClickHouse deployment
        ↓
read-only analytical tools
        ↓
FastAPI Tool API
        ↓
Yandex Cloud AI Studio agent
        ↓
Airflow orchestration and scheduled refresh
```

Бизнес-логика ingestion, нормализации и data-quality checks должна оставаться независимой от конкретной облачной инфраструктуры.
