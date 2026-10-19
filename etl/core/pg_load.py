"""Writes to the target Postgres schema, set-based (legacy ran one INSERT per row).

- `upsert`: COPY the rows into a temp stage, then one INSERT ... ON CONFLICT (key) DO UPDATE.
  When a key repeats inside one batch (a page fetched twice), the last row wins; without that,
  ON CONFLICT fails with "cannot affect row a second time".
- `run_sql_file`: runs a transform file, statement by statement (`--;;` lines split them).
  Values are bound parameters (`%(org)s`, `%(start)s`, `%(end)s` ...) and never pasted into the
  SQL text; legacy filled `${org_id}` with string templating. A literal `%` in a file is `%%`.

Table and column names come from pipeline constants, must match IDENT, and are composed with
psycopg.sql. Values are written as given: wrap dicts in `Jsonb`, and convert timestamps the
way the column expects before they get here (a timestamp-without-time-zone column drops an
offset silently).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, LiteralString

from psycopg import Connection, sql

IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
SEPARATOR = "\n--;;\n"
_STAGE = sql.Identifier("_etl_stage")


def _ident(name: str) -> sql.Identifier:
    if not IDENT.match(name):
        raise ValueError(f"Not a plain identifier: {name!r}")
    return sql.Identifier(name)


def _names(names: Sequence[str]) -> sql.Composed:
    return sql.SQL(", ").join(_ident(n) for n in names)


def upsert(conn: Connection[Any], table: str, columns: Sequence[str], key: Sequence[str],
           rows: Sequence[Sequence[Any]], *, keep_existing: Sequence[str] = (),
           conflict: LiteralString | None = None) -> int:  # fmt: skip
    """Insert or update `rows` on `key`. Returns the rows written. A column in
    `keep_existing` keeps the stored value when the new one is NULL (COALESCE). `conflict`
    replaces the key in DISTINCT ON and ON CONFLICT when the unique index is on expressions
    (e.g. "organization_id, COALESCE(ad_group_id, 0)"). Runs in the caller's transaction: the
    caller commits."""
    if not rows:
        return 0
    if not key or not set(key) <= set(columns):
        raise ValueError(f"{table}: the key {list(key)} must be among the columns")
    target, cols = _ident(table), _names(columns)
    keys: sql.Composable = sql.SQL(conflict) if conflict else _names(key)
    rest = [c for c in columns if c not in key]
    sets = [
        sql.SQL("{c} = COALESCE(EXCLUDED.{c}, {t}.{c})").format(c=_ident(c), t=target)
        if c in keep_existing
        else sql.SQL("{c} = EXCLUDED.{c}").format(c=_ident(c))
        for c in rest
    ]
    action = sql.SQL("DO UPDATE SET ") + sql.SQL(", ").join(sets) if sets else sql.SQL("DO NOTHING")
    with conn.cursor() as cur:
        cur.execute(sql.SQL("DROP TABLE IF EXISTS pg_temp.{s}").format(s=_STAGE))
        cur.execute(
            sql.SQL("CREATE TEMP TABLE {s} ON COMMIT DROP AS SELECT {c} FROM {t} WITH NO DATA")
            .format(s=_STAGE, c=cols, t=target)
        )  # fmt: skip
        cur.execute(sql.SQL("ALTER TABLE {s} ADD COLUMN _seq bigserial").format(s=_STAGE))
        with cur.copy(sql.SQL("COPY {s} ({c}) FROM STDIN").format(s=_STAGE, c=cols)) as copy:
            for row in rows:
                copy.write_row(row)
        cur.execute(
            sql.SQL(
                "INSERT INTO {t} ({c}) SELECT DISTINCT ON ({k}) {c} FROM {s}"
                " ORDER BY {k}, _seq DESC ON CONFLICT ({k}) {a}"
            ).format(t=target, c=cols, k=keys, s=_STAGE, a=action)
        )
        written = cur.rowcount
        cur.execute(sql.SQL("DROP TABLE pg_temp.{s}").format(s=_STAGE))
    return written


def _comment_only(part: str) -> bool:
    return all(not ln.strip() or ln.lstrip().startswith("--") for ln in part.splitlines())


def statements(text: str) -> list[str]:
    """The statements of a transform file; comment-only parts are skipped."""
    return [p.strip() for p in text.split(SEPARATOR) if not _comment_only(p)]


def run_sql_file(conn: Connection[Any], path: Path, params: Mapping[str, Any]) -> list[int]:
    """Run each statement of `path` with `params` bound. Returns each statement's row count.
    Runs in the caller's transaction: the caller commits."""
    counts: list[int] = []
    with conn.cursor() as cur:
        for stmt in statements(path.read_text(encoding="utf-8")):
            cur.execute(stmt.encode("utf-8"), dict(params))  # bytes: psycopg takes it as-is
            counts.append(cur.rowcount)
    return counts
