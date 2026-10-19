"""Alembic for the ETL's tables, in the target schema (POSTGRES__TARGET_SCHEMA).

The version table is `alembic_version_etl` in that schema, so the backend's Alembic
(`alembic_version` in public) and this one never see each other's history. The schema is
created first when it does not exist (sandbox parity runs use `etl_v2`). The connection's
search_path is that schema alone, so migrations use unqualified table names.
"""

from __future__ import annotations

import psycopg
from sqlalchemy import Connection, create_engine, text

from alembic import context
from etl.core.db import pg_conninfo
from etl.core.settings import load_settings

VERSION_TABLE = "alembic_version_etl"


def _migrate(connection: Connection, schema: str) -> None:
    context.configure(
        connection=connection,
        version_table=VERSION_TABLE,
        version_table_schema=schema,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    postgres = load_settings().postgres
    schema = postgres.target_schema  # validated: ^[a-z_][a-z0-9_]{0,62}$
    engine = create_engine(
        "postgresql+psycopg://", creator=lambda: psycopg.connect(pg_conninfo(postgres))
    )
    with engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))  # validated identifier
        connection.commit()
        _migrate(connection, schema)
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline mode is not supported: run against a database.")
run_online()
