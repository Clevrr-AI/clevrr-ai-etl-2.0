"""Typed wrappers over clickhouse-connect (its client methods are only partly typed).

Values always go as parameters (`{name:Type}` in the SQL); never into the SQL text.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from clickhouse_connect.driver.client import Client as ClickHouseClient


def command(client: ClickHouseClient, sql: str, params: Mapping[str, Any] | None = None, *,
            settings: Mapping[str, Any] | None = None) -> None:  # fmt: skip
    client.command(  # pyright: ignore[reportUnknownMemberType]
        sql, parameters=dict(params or {}), settings=dict(settings or {})
    )


def rows(client: ClickHouseClient, sql: str,
         params: Mapping[str, Any] | None = None) -> list[tuple[Any, ...]]:  # fmt: skip
    result = client.query(sql, parameters=dict(params or {}))  # pyright: ignore[reportUnknownMemberType]
    return [tuple(r) for r in result.result_rows]  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]


def insert(client: ClickHouseClient, table: str, data: Sequence[Sequence[Any]],
           columns: Sequence[str]) -> int:  # fmt: skip
    if not data:
        return 0
    client.insert(table, [list(r) for r in data], column_names=list(columns))  # pyright: ignore[reportUnknownMemberType]
    return len(data)
