# One image for every job. The existing Kubernetes CronJobs (cronjobs_dev/, cronjobs/) run it
# with legacy's settings (entrypoint.sh -> `etl legacy`); locally and on Cloud Run the arguments
# are the `etl` command. Build: docker build -t clevrr-etl-v2 .

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
# The Cloud SQL proxy, as in legacy's image (ETL/Dockerfile): the CronJobs reach Postgres
# through it on 127.0.0.1 with the pod's Workload Identity.
COPY --from=gcr.io/cloud-sql-connectors/cloud-sql-proxy:2.11.0 /cloud-sql-proxy /usr/local/bin/cloud-sql-proxy
WORKDIR /app
COPY --from=builder --chown=etl:etl /app /app
COPY --chown=etl:etl entrypoint.sh /app/entrypoint.sh
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER etl
ENTRYPOINT ["tini", "--", "/app/entrypoint.sh"]
CMD ["list"]
