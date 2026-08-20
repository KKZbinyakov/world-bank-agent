FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --upgrade pip build \
    && python -m build --wheel


FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=prod \
    LOG_LEVEL=INFO \
    API_HOST=0.0.0.0 \
    API_DOCS_ENABLED=false \
    MARTS_CONFIG_PATH=/app/configs/marts.yaml

WORKDIR /app

RUN useradd \
    --create-home \
    --uid 10001 \
    --shell /usr/sbin/nologin \
    app

COPY --from=builder /build/dist /tmp/dist

RUN python -m pip install --no-cache-dir /tmp/dist/*.whl \
    && rm -rf /tmp/dist

COPY configs ./configs

USER app

EXPOSE 8000

CMD ["python", "-m", "wb_insight.api.main"]