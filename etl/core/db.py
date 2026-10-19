"""Database clients. The ETL is a batch process, so the clients are synchronous.

Postgres: a psycopg pool whose connections see only the target schema (search_path), so an
unqualified table name can never reach another schema. ClickHouse: clickhouse-connect with
timeouts on the target database; legacy's raw HTTP calls are gone.
"""

from __future__ import annotations

import clickhouse_connect
from clickhouse_connect.driver.client import Client as ClickHouseClient
from psycopg import Connection
from psycopg.conninfo import make_conninfo
from psycopg.rows import TupleRow
from psycopg_pool import ConnectionPool

from etl.core.settings import ClickHouseSettings, PostgresSettings

APP_NAME = "clevrr-etl-v2"


def pg_conninfo(settings: PostgresSettings) -> str:
    return make_conninfo(
        host=settings.host,
        port=settings.port,
        dbname=settings.db,
        user=settings.user,
        password=settings.password.get_secret_value(),
        connect_timeout=settings.connect_timeout_s,
        application_name=APP_NAME,
        # Only the target schema: an unqualified name never falls back to `public`.
        options=f"-c search_path={settings.target_schema} -c TimeZone={settings.session_timezone}",
    )


def open_pg_pool(settings: PostgresSettings) -> ConnectionPool[Connection[TupleRow]]:
    pool: ConnectionPool[Connection[TupleRow]] = ConnectionPool(
        pg_conninfo(settings),
        connection_class=Connection[TupleRow],
        min_size=1,
        max_size=settings.pool_max,
        open=False,
        name=APP_NAME,
    )
    pool.open(wait=True, timeout=settings.connect_timeout_s * 3)
    return pool


def open_clickhouse(
    settings: ClickHouseSettings, *, database: str | None = None
) -> ClickHouseClient:
    """`database` overrides the target (ch-migrate connects to `default`: the target may not
    exist yet)."""
    return clickhouse_connect.get_client(  # pyright: ignore[reportUnknownMemberType]
        host=settings.host,
        port=settings.port,
        username=settings.user,
        password=settings.password.get_secret_value(),
        database=database or settings.database,
        secure=settings.secure,
        connect_timeout=settings.connect_timeout_s,
        send_receive_timeout=settings.query_timeout_s + 30,
        settings={"max_execution_time": settings.query_timeout_s},
    )
