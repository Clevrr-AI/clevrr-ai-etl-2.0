"""The `etl` command (one entry point for every Cloud Run Job and every local run).

    etl run <pipeline> [--org ID ...] [--from YYYY-MM-DD --to YYYY-MM-DD]
    etl sweep            mark stale `running` runs `killed`
    etl legacy           run from legacy's CronJob settings (ETL_COMMAND, ETL_* ...)
    etl pg-migrate       apply pending Postgres migrations (alembic) to the target schema
    etl ch-migrate       apply pending ClickHouse migrations (clickhouse_migrations/)
    etl list             the registered pipelines

`run` takes the orgs with an enabled connector for the pipeline's source, keeps those named
by --org (default: all), then those in RUN__ORG_ALLOWLIST (when set), then this task's shard
(CLOUD_RUN_TASK_INDEX of CLOUD_RUN_TASK_COUNT). Exit code 1 when any org failed.
"""

from __future__ import annotations

import argparse
import signal
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from pathlib import Path
from types import FrameType

import httpx

from etl import pipelines
from etl.core.alerts import build_alerts
from etl.core.ch_migrate import migrate
from etl.core.connector_table import TableConnectorSource
from etl.core.connectors import shard
from etl.core.db import open_clickhouse, open_pg_pool
from etl.core.logging import configure_logging, get_logger
from etl.core.model import Org
from etl.core.run_ledger import PgLedger
from etl.core.settings import Settings, load_settings
from etl.runner import Runner

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
log = get_logger(__name__)


def _day(text: str) -> datetime:
    return datetime.combine(date.fromisoformat(text), time.min, tzinfo=UTC)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="etl")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run a pipeline")
    run.add_argument("pipeline")
    run.add_argument("--org", action="append", default=[], help="org id; repeat; default all")
    run.add_argument("--from", dest="start", type=_day, help="backfill start day (UTC)")
    run.add_argument("--to", dest="end", type=_day, help="backfill end day (UTC, exclusive)")
    sub.add_parser("sweep", help="mark stale running runs killed")
    sub.add_parser("pg-migrate", help="apply pending Postgres migrations to the target schema")
    sub.add_parser("legacy", help="run from legacy's CronJob settings (ETL_COMMAND, ETL_*)")
    sub.add_parser("ch-migrate", help="apply pending ClickHouse migrations to the target database")
    sub.add_parser("list", help="list pipelines")
    return parser


def select_orgs(found: Sequence[Org], wanted: Sequence[str], settings: Settings) -> list[Org]:
    orgs = [o for o in found if not wanted or o.org_id in wanted]
    allow = settings.run.org_allowlist
    if allow:
        orgs = [o for o in orgs if o.org_id in allow]
    return shard(orgs, index=settings.task_index, count=settings.task_count)


def _on_sigterm(signum: int, frame: FrameType | None) -> None:
    raise SystemExit(128 + signum)  # the runner records `killed`, then the process ends


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "list":
        for name, p in sorted(pipelines.PIPELINES.items()):
            sys.stdout.write(f"{name}\tsource={p.source}\tsteps={len(p.steps)}\n")
        return 0
    if args.command == "legacy":
        return _legacy()
    settings = load_settings()
    configure_logging(settings.log_level)
    if args.command == "pg-migrate":
        from alembic.config import Config

        from alembic import command

        command.upgrade(Config(str(ALEMBIC_INI)), "head")
        log.info("etl.pg_migrate_done", schema=settings.postgres.target_schema)
        return 0
    if args.command == "ch-migrate":
        client = open_clickhouse(settings.clickhouse, database="default")
        try:
            applied = migrate(client, settings.clickhouse)
        finally:
            client.close()
        log.info("etl.ch_migrate_done", applied=applied, database=settings.clickhouse.database)
        return 0
    signal.signal(signal.SIGTERM, _on_sigterm)
    pool = open_pg_pool(settings.postgres)
    try:
        ledger = PgLedger(pool)
        if args.command == "sweep":
            killed = ledger.sweep(settings.run.stale_after_s)
            log.info("etl.swept", killed=len(killed))
            return 0
        return _run(args, settings, ledger)
    finally:
        pool.close()


def _run(args: argparse.Namespace, settings: Settings, ledger: PgLedger) -> int:
    pipeline = pipelines.get(args.pipeline)
    found = TableConnectorSource(ledger.pool).orgs(pipeline.source)
    orgs = select_orgs(found, args.org, settings)
    ch = open_clickhouse(settings.clickhouse)
    with httpx.Client() as http:
        runner = Runner(settings, ledger.pool, ch, ledger, build_alerts(settings.alerts, http))
        log.info("etl.started", pipeline=pipeline.name, orgs=len(orgs),
                 task=f"{settings.task_index}/{settings.task_count}")  # fmt: skip
        summary = runner.run(pipeline, orgs, start=args.start, end=args.end)
    ch.close()
    log.info("etl.finished", pipeline=pipeline.name, orgs=len(orgs), failed=len(summary.failed))
    return 0 if summary.ok else 1


if __name__ == "__main__":
    sys.exit(main())


def _legacy() -> int:
    """One legacy job = one or more `etl run` invocations; the first failure stops it."""
    from etl import legacy

    job = legacy.read_job()
    configure_logging(job.log_level)
    log.info("etl.legacy_job", job=job.name, runs=[" ".join(r) for r in job.runs],
             translated=job.translated)  # fmt: skip
    for argv in job.runs:
        code = main(argv)
        if code:
            return code
    return 0
