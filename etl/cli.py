"""The `etl` command (one entry point for every Cloud Run Job and every local run).

    etl run <pipeline> [--org ID ...] [--from YYYY-MM-DD --to YYYY-MM-DD]
    etl tick <source>    run the source's pipelines for the orgs whose interval is due
    etl schedule list|set|unset   per-org sync intervals (etl_schedules)
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
from etl.core.api_client import ApiError, InternalApi, build_api
from etl.core.ch_migrate import migrate
from etl.core.connector_table import TableConnectorSource
from etl.core.connectors import select_orgs
from etl.core.db import open_clickhouse, open_pg_pool
from etl.core.logging import configure_logging, get_logger
from etl.core.model import Pipeline, Status
from etl.core.run_ledger import PgLedger
from etl.core.schedules import ScheduleTable, parse_every
from etl.core.settings import Settings, load_settings
from etl.runner import Runner, RunSummary
from etl.scheduler import SCHEDULED, tick

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
    tick_ = sub.add_parser("tick", help="run a source's pipelines for the orgs that are due")
    tick_.add_argument("source", choices=sorted(SCHEDULED))
    _schedule_parser(sub.add_parser("schedule", help="per-org sync intervals"))
    sub.add_parser("sweep", help="mark stale running runs killed")
    sub.add_parser("pg-migrate", help="apply pending Postgres migrations to the target schema")
    sub.add_parser("legacy", help="run from legacy's CronJob settings (ETL_COMMAND, ETL_*)")
    sub.add_parser("ch-migrate", help="apply pending ClickHouse migrations to the target database")
    sub.add_parser("list", help="list pipelines")
    return parser


def _schedule_parser(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True)
    listing = sub.add_parser("list", help="the schedules (orgs with no row run daily)")
    listing.add_argument("--source", help="one source only")
    set_ = sub.add_parser("set", help="set an org's interval for a source")
    set_.add_argument("--org", required=True)
    set_.add_argument("--source", required=True, choices=sorted(SCHEDULED))
    set_.add_argument("--every", required=True, type=parse_every,
                      help="1h, 2h, 3h, 4h, 6h, 8h, 12h or daily")  # fmt: skip
    set_.add_argument("--paused", action="store_true", help="keep the row, run nothing")
    set_.add_argument("--note", help="why (free text)")
    unset = sub.add_parser("unset", help="remove the row: the org runs daily again")
    unset.add_argument("--org", required=True)
    unset.add_argument("--source", required=True, choices=sorted(SCHEDULED))


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
        if args.command == "schedule":
            return _schedule(args, ScheduleTable(pool))
        if args.command == "tick":
            return _tick(args.source, settings, ledger)
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
        _signal_landed(build_api(settings.api, http), pipeline, summary)
    ch.close()
    log.info("etl.finished", pipeline=pipeline.name, orgs=len(orgs), failed=len(summary.failed))
    return 0 if summary.ok else 1


# The pipelines whose success means "this connector's data landed" for onboarding. The
# catalog and the region mirror are extras of a connector, as legacy's hook skipped them.
LANDED_PROVIDERS = {"shopify-sales": "shopify", "meta-ads": "meta_ads", "google-ads": "google_ads"}


def _signal_landed(api: InternalApi | None, pipeline: Pipeline, summary: RunSummary) -> None:
    """Tell the API about each org whose run succeeded (API__BASE_URL set). A failure here
    is logged: the data is loaded; the API's flags can be set again by the next run."""
    provider = LANDED_PROVIDERS.get(pipeline.name)
    if api is None or provider is None:
        return
    for outcome in summary.outcomes:
        if outcome.status is not Status.SUCCEEDED or outcome.window is None:
            continue
        window = (outcome.window.start.isoformat(), outcome.window.end.isoformat())
        try:
            api.landed(org_id=outcome.org_id, provider=provider, run_id=outcome.job_id,
                       mode=outcome.mode.value if outcome.mode else "incremental",
                       window=window, tables=[pipeline.name])  # fmt: skip
        except (ApiError, httpx.HTTPError) as exc:
            log.warning("etl.landed_failed", org_id=outcome.org_id, error_type=type(exc).__name__)


def _tick(source: str, settings: Settings, ledger: PgLedger) -> int:
    found = TableConnectorSource(ledger.pool).orgs(source)
    orgs = select_orgs(found, [], settings)
    schedules = ScheduleTable(ledger.pool).for_source(source)
    ch = open_clickhouse(settings.clickhouse)
    with httpx.Client() as http:
        runner = Runner(settings, ledger.pool, ch, ledger, build_alerts(settings.alerts, http))
        log.info("etl.tick_started", source=source, orgs=len(orgs), schedules=len(schedules),
                 task=f"{settings.task_index}/{settings.task_count}")  # fmt: skip
        summaries = tick(source, orgs, schedules, runner, ledger)
        api = build_api(settings.api, http)
        for summary in summaries:
            _signal_landed(api, pipelines.get(summary.pipeline), summary)
    ch.close()
    failed = sum(len(s.failed) for s in summaries)
    log.info("etl.tick_finished", source=source, ran=[s.pipeline for s in summaries],
             failed=failed)  # fmt: skip
    return 0 if failed == 0 else 1


def _schedule(args: argparse.Namespace, table: ScheduleTable) -> int:
    out = sys.stdout
    if args.action == "set":
        row = table.set(args.org, args.source, args.every, enabled=not args.paused,
                        note=args.note)  # fmt: skip
        out.write(f"{row.org_id}\t{row.source}\t{'paused' if args.paused else row.every}\n")
        return 0
    if args.action == "unset":
        removed = table.unset(args.org, args.source)
        out.write(f"{args.org}\t{args.source}\t{'daily (row removed)' if removed else 'no row'}\n")
        return 0
    rows = [r for r in table.all() if not args.source or r.source == args.source]
    out.write("org_id\tsource\tevery\tenabled\tupdated_at\tnote\n")
    for r in rows:
        when = r.updated_at.isoformat(timespec="minutes") if r.updated_at else ""
        out.write(f"{r.org_id}\t{r.source}\t{r.every}\t{r.enabled}\t{when}\t{r.note or ''}\n")
    if not rows:
        out.write("(no rows: every org runs daily)\n")
    return 0


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
