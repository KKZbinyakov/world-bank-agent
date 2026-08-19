PYTHON ?= python

.PHONY: install install-dev format format-check lint typecheck test check doctor show-config api smoke-api clean

install:
	$(PYTHON) -m pip install -r requirements.txt

install-dev:
	$(PYTHON) -m pip install -r requirements-dev.txt

format:
	$(PYTHON) -m ruff check --fix .
	$(PYTHON) -m ruff format .

format-check:
	$(PYTHON) -m ruff format --check .

lint:
	$(PYTHON) -m ruff check .

typecheck:
	$(PYTHON) -m mypy src

test:
	$(PYTHON) -m pytest

check: lint format-check typecheck test

doctor:
	$(PYTHON) -m wb_insight doctor

show-config:
	$(PYTHON) -m wb_insight show-config

api:
	$(PYTHON) -m wb_insight.api.main

smoke-api:
	$(PYTHON) scripts/smoke_test_api.py

clean:
	$(PYTHON) -c "from pathlib import Path; import shutil; [shutil.rmtree(p, ignore_errors=True) for p in map(Path, ['.pytest_cache', '.mypy_cache', '.ruff_cache', 'htmlcov', 'build', 'dist'])]"
