"""Writes to ClickHouse, per org (legacy read every org's rows, then filtered in pandas).

- `append`: insert in batches. For ReplacingMergeTree tables (orders, order_lines, refunds ...)
  that collapse versions on their own; readers use FINAL.
- `replace`: delete the org's slice (optionally a time window, optionally one source) and
  insert the new rows. On a cluster the delete runs on `<table>_shard ON CLUSTER` (a Distributed
  table takes no mutation) and waits for every replica (mutations_sync=2); then the slice must
  read empty before the insert, else the step fails. A failed run reruns cleanly: the delete
  comes first again.

Identifiers (table, columns) come from pipeline constants and must match IDENT; every value is a
bound parameter. On a cluster, an insert into a Distributed table is synchronous
(insert_distributed_sync=1) and waits for a majority of each shard's replicas
(insert_quorum='auto'), so a check right after the load sees the rows on any replica.

Inserts never deduplicate (insert_deduplicate=0). A Replicated table drops a block whose hash it
saw recently, even after a delete: a rerun of unchanged data then lost rows on sandbox
(2026-10-17, costs, `rand()` sharding). Idempotency comes from `replace` (delete first) and from
ReplacingMergeTree, never from block hashes.

Values are converted to each column's type first (`ch_types`, from `system.columns`): a NULL
in a non-Nullable column becomes its default, as legacy's Parquet load did. A naive datetime is
written as UTC; clickhouse-connect would otherwise read it in the machine's local zone (a laptop
in IST shifts it 5.5 hours).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from string import Template
from typing import Any

from clickhouse_connect.driver.client import Client as ClickHouseClient

from etl.core import ch
from etl.core.ch_types import Coerce, coercer
from etl.core.model import StepFailed
from etl.core.settings import ClickHouseSettings

IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_DELETE = (
    "ALTER TABLE ${target}${on_cluster} DELETE WHERE ${where} SETTINGS mutations_sync = ${sync}"
)
_COUNT = "SELECT count() FROM ${table} WHERE ${where}"
_TYPES = (
    "SELECT name, type FROM system.columns"
    " WHERE database = currentDatabase() AND table = {t:String} ORDER BY position"
)


@dataclass(frozen=True, slots=True)
class Slice:
    """Which rows of one org a `replace` deletes first."""

    org_column: str = "organization_id"
    time_column: str | None = None  # with start/end: [start, end)
    start: datetime | None = None
    end: datetime | None = None
    source_column: str | None = None  # with source: one source_type only
    source: str | None = None

    def where(self) -> tuple[str, dict[str, Any]]:
        parts = [_ident(self.org_column) + " = {org:String}"]
        params: dict[str, Any] = {}
        if self.time_column:
            if self.start is None or self.end is None:
                raise ValueError("A time slice needs start and end.")
            col = _ident(self.time_column)
            parts.append(col + " >= {start:DateTime64(3)} AND " + col + " < {end:DateTime64(3)}")
            params |= {"start": self.start, "end": self.end}
        if self.source_column:
            parts.append(_ident(self.source_column) + " = {source:String}")
            params["source"] = self.source
        return " AND ".join(parts), params


def _ident(name: str) -> str:
    if not IDENT.match(name):
        raise ValueError(f"Not a plain identifier: {name!r}")
    return name


class ChWriter:
    def __init__(self, client: ClickHouseClient, settings: ClickHouseSettings) -> None:
        self.client = client
        self.cluster = settings.cluster
        self.batch_rows = settings.insert_batch_rows
        self._types: dict[str, dict[str, Coerce]] = {}

    def _load_types(self, table: str) -> dict[str, Coerce]:
        if table not in self._types:
            found = ch.rows(self.client, _TYPES, {"t": _ident(table)})
            if not found:
                raise ValueError(f"ClickHouse has no table {table}")
            self._types[table] = {str(name): coercer(str(kind)) for name, kind in found}
        return self._types[table]

    def columns(self, table: str) -> list[str]:
        """The table's columns, in ClickHouse order."""
        return list(self._load_types(table))

    def coercers(self, table: str, columns: Sequence[str]) -> list[Coerce]:
        """One conversion per column, from the table's types (read once per table)."""
        types = self._load_types(table)
        missing = [c for c in columns if c not in types]
        if missing:
            raise ValueError(f"{table} has no column {missing}")
        return [types[c] for c in columns]

    def append(self, table: str, columns: Sequence[str], data: Sequence[Sequence[Any]]) -> int:
        name = _ident(table)
        cols = [_ident(c) for c in columns]
        convert = self.coercers(name, cols)
        settings: dict[str, Any] = {"insert_deduplicate": 0}
        if self.cluster:
            settings |= {"insert_distributed_sync": 1, "insert_quorum": "auto"}
        written = 0
        for i in range(0, len(data), self.batch_rows):
            batch = [[f(v) for f, v in zip(convert, r, strict=True)]
                     for r in data[i : i + self.batch_rows]]  # fmt: skip
            self.client.insert(  # pyright: ignore[reportUnknownMemberType]
                name, batch, column_names=cols, settings=settings
            )
            written += len(batch)
        return written

    def replace(self, table: str, org_id: str, slice_: Slice, columns: Sequence[str],
                data: Sequence[Sequence[Any]]) -> int:  # fmt: skip
        name = _ident(table)
        where, params = slice_.where()
        params["org"] = org_id
        target = name + "_shard" if self.cluster else name
        on_cluster = " ON CLUSTER '" + self.cluster + "'" if self.cluster else ""
        sync = "2" if self.cluster else "1"
        ch.command(self.client, Template(_DELETE).substitute(
            target=target, on_cluster=on_cluster, where=where, sync=sync), params)  # fmt: skip
        left = ch.rows(self.client, Template(_COUNT).substitute(table=name, where=where), params)
        if left and int(left[0][0]) > 0:
            count = int(left[0][0])
            message = f"{name}: {count} rows of {org_id} are still there after the delete"
            raise StepFailed(message, detail={"table": name, "left": count})
        return self.append(name, columns, data)
