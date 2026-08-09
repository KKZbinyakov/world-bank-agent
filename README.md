# WB Insight Agent

Агентская аналитическая система для получения, подготовки и исследования макроэкономических и социальных показателей Всемирного банка.

Пользователь формулирует вопрос на естественном языке, а система подбирает показатели, обращается к аналитическим витринам, выполняет расчеты и возвращает проверяемые выводы со ссылкой на дашборд.

## Статус проекта

Репозиторий находится на этапе **первого коммита**. В текущую версию входят:

- базовая структура Python-проекта;
- типизированная конфигурация приложения;
- конфигурация исследования, показателей и групп стран;
- минимальный CLI для проверки окружения;
- тесты, линтер, форматтер и проверка типов;
- GitHub Actions для автоматического контроля качества.

Клиент World Bank API, ETL-pipeline, ClickHouse, DataLens и агент будут добавляться отдельными pull request.

## Исследовательская задача

Основная исследовательская гипотеза:

> Существует ли связь между уровнем экономического развития страны и выбранными социальными, инфраструктурными и экологическими показателями, и как эта связь меняется во времени для разных групп стран?

Конкретный период, пилотные страны и индикаторы задаются в каталоге `configs/` и не зашиваются в Python-код.

## Планируемый сквозной сценарий

```text
World Bank API
      ↓
сырые JSON-ответы
      ↓
очистка и нормализация
      ↓
проверки качества
      ↓
ClickHouse и аналитические витрины
      ↓
DataLens и инструменты агента
      ↓
проверяемое аналитическое саммари
```

## Требования

- Python 3.12;
- Git;
- PowerShell, CMD, Git Bash, WSL или Unix-подобный терминал.

## Быстрый запуск

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
python -m wb_insight doctor
python -m wb_insight show-config
```

Если выполнение скриптов PowerShell запрещено политикой системы, для текущего окна можно выполнить:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### Linux, macOS, WSL или Git Bash

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
cp .env.example .env
python -m wb_insight doctor
python -m wb_insight show-config
```

После установки пакет также предоставляет команду:

```bash
wb-insight doctor
```

## Проверка качества

Прямые команды, работающие на любой поддерживаемой платформе:

```bash
python -m ruff check .
python -m ruff format --check .
python -m mypy src
python -m pytest
```

При наличии `make` можно выполнить все проверки одной командой:

```bash
make check
```

Для установки локальных pre-commit hooks:

```bash
pre-commit install
pre-commit run --all-files
```

## Конфигурация

Основные файлы:

- `configs/research.yaml` — период, вопросы, страны и правила анализа;
- `configs/indicators.yaml` — реестр показателей World Bank;
- `configs/country_groups.yaml` — пользовательские группы стран;
- `.env` — настройки конкретного окружения, не добавляемые в Git;
- `.env.example` — безопасный шаблон переменных окружения.

В конфигурации показателя используются:

- `code` — код World Bank;
- `alias` — стабильное внутреннее имя;
- `role` — `target`, `feature` или `context`;
- `enabled` — включен ли показатель в текущий scope.

## Структура репозитория

```text
.
├── .github/workflows/ci.yml
├── configs/
│   ├── country_groups.yaml
│   ├── indicators.yaml
│   └── research.yaml
├── data/
│   ├── processed/.gitkeep
│   └── raw/.gitkeep
├── docs/architecture.md
├── notebooks/.gitkeep
├── src/wb_insight/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── config.py
│   └── logging.py
├── tests/
│   ├── test_cli.py
│   └── test_config.py
├── .env.example
├── .gitignore
├── .pre-commit-config.yaml
├── Makefile
├── pyproject.toml
├── requirements-dev.txt
├── requirements.txt
└── README.md
```

## Команды CLI

Проверить доступность и согласованность конфигурации:

```bash
python -m wb_insight doctor
```

Показать безопасное сводное представление конфигурации:

```bash
python -m wb_insight show-config
```

## Первый технический milestone

Следующий рабочий этап должен обеспечить сценарий:

1. получить данные для трех стран и двух индикаторов через World Bank API;
2. сохранить исходные ответы в `data/raw`;
3. нормализовать наблюдения;
4. выполнить проверки качества;
5. сохранить результат в Parquet;
6. покрыть API-клиент unit-тестами без обращения к реальному API.

## Правила работы с Git

- изменения выполняются в отдельных feature-ветках;
- прямой push в `main` не рекомендуется;
- перед pull request запускается `python -m pytest` и проверки Ruff;
- новые секреты и реальные выгрузки данных в Git не добавляются;
- крупная функциональность оформляется отдельным pull request.

Подробности находятся в [CONTRIBUTING.md](CONTRIBUTING.md).

## Участники

Заполнить после утверждения ролей:

- участник 1 — Data Engineering;
- участник 2 — Analytics и BI;
- участник 3 — Backend и LLM-агент;
- участник 4 — Product Analytics и QA.
