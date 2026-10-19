"""ClickHouse migrations (plan 7.6): `clickhouse_migrations/NNNN_description.sql`, applied in
order and recorded in `ch_migrations`, in the configured database.

One file set for every target. Files use `${db}` and `${cluster}`; the runner fills them from
the settings. On a cluster (`CLICKHOUSE__CLUSTER` set) statements run as written: ON CLUSTER,
Replicated engines, Distributed tables over `X_shard`. On a single server (local, tests) the
same file is rendered single-node: no ON CLUSTER, no Distributed, `X_shard` becomes `X`, plain
engines, no TTL. A statement whose first line is `-- cluster only` (a Distributed table's DROP,
whose name is the real table on one server) is skipped there. Every statement must be
idempotent (IF NOT EXISTS / IF EXISTS): a rerun is safe.
"""

from __future__ import annotations

import re
from pathlib import Path
from string import Template

from clickhouse_connect.driver.client import Client as ClickHouseClient

from etl.core import ch
from etl.core.logging import get_logger
from etl.core.settings import ClickHouseSettings

log = get_logger(__name__)
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "clickhouse_migrations"
SEPARATOR = "\n--;;\n"
_FILE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
_ON_CLUSTER = re.compile(r"\s+ON CLUSTER '[^']*'")
_REPLICATED = re.compile(r"Replicated(\w*MergeTree)\('[^']*',\s*'[^']*'(?:,\s*)?")
_SHARD = re.compile(r"\b(\w+)_shard\b")
_TTL = re.compile(r"^TTL .*$\n?", re.MULTILINE)

_LEDGER_CLUSTER = (
    "CREATE TABLE IF NOT EXISTS ${db}.ch_migrations ON CLUSTER '${cluster}'"
    " (version String, applied_at DateTime DEFAULT now())"
    " ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/${db}/ch_migrations', '{replica}')"
    " ORDER BY version"
)
_CREATE_DB = "CREATE DATABASE IF NOT EXISTS ${db}${on_cluster}"
_APPLIED = "SELECT version FROM ${db}.ch_migrations FINAL"
_LEDGER_SINGLE = (
    "CREATE TABLE IF NOT EXISTS ${db}.ch_migrations"
    " (version String, applied_at DateTime DEFAULT now())"
    " ENGINE = ReplacingMergeTree ORDER BY version"
)


def migration_files(folder: Path = MIGRATIONS_DIR) -> list[Path]:
    files = sorted(p for p in folder.glob("*.sql") if _FILE.match(p.name))
    versions = [_FILE.match(p.name).group(1) for p in files]  # pyright: ignore[reportOptionalMemberAccess]
    if len(versions) != len(set(versions)):
        raise SystemExit("Two ClickHouse migrations share a version number.")
    return files


CLUSTER_ONLY = "-- cluster only"


def _comment_only(part: str) -> bool:
    return all(not ln.strip() or ln.lstrip().startswith("--") for ln in part.splitlines())


def single_node(stmt: str) -> str | None:
    """A cluster statement for one server, or None when it has no single-node form."""
    if "ENGINE = Distributed(" in stmt or stmt.lstrip().startswith(CLUSTER_ONLY):
        return None
    stmt = _ON_CLUSTER.sub("", stmt)
    stmt = _REPLICATED.sub(r"\1(", stmt)
    stmt = _SHARD.sub(r"\1", stmt)
    return _TTL.sub("", stmt)


def render(sql: str, settings: ClickHouseSettings) -> list[str]:
    cluster = settings.cluster or "single"
    text = Template(sql).substitute(db=settings.database, cluster=cluster)
    stmts = [s.strip() for s in text.split(SEPARATOR) if not _comment_only(s)]
    if settings.cluster:
        return stmts
    return [s for s in (single_node(x) for x in stmts) if s]


def migrate(client: ClickHouseClient, settings: ClickHouseSettings,
            folder: Path = MIGRATIONS_DIR) -> list[str]:  # fmt: skip
    """Apply the pending migrations. Returns the versions applied now."""
    db = settings.database  # a validated identifier, like the cluster name
    on_cluster = " ON CLUSTER '" + settings.cluster + "'" if settings.cluster else ""
    ch.command(client, Template(_CREATE_DB).substitute(db=db, on_cluster=on_cluster))
    ledger = _LEDGER_CLUSTER if settings.cluster else _LEDGER_SINGLE
    ch.command(client, Template(ledger).substitute(db=db, cluster=settings.cluster or ""))
    done = {str(r[0]) for r in ch.rows(client, Template(_APPLIED).substitute(db=db))}
    applied: list[str] = []
    for path in migration_files(folder):
        version = path.name[:4]
        if version in done:
            continue
        for stmt in render(path.read_text(encoding="utf-8"), settings):
            ch.command(client, stmt)
        ch.insert(client, db + ".ch_migrations", [[version]], ["version"])
        log.info("etl.ch_migrated", version=version, file=path.name, database=db)
        applied.append(version)
    return applied
