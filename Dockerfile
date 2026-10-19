# One image for every pipeline. Each Cloud Run Job runs `etl run <pipeline>` with its own args
# (infra/), and `etl sweep` runs on a schedule. Build: docker build -t clevrr-etl-v2 .

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY etl ./etl
COPY alembic ./alembic
COPY alembic.ini ./
COPY clickhouse_migrations ./clickhouse_migrations
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 etl
WORKDIR /app
COPY --from=builder --chown=etl:etl /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER etl
ENTRYPOINT ["tini", "--", "etl"]
CMD ["list"]
