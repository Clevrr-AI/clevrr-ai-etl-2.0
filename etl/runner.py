"""The Runner (plan 7.2): runs one pipeline for a set of orgs.

- Per-org isolation: one org's failure never stops the others.
- Steps run in order; the first failed step stops that org's run.
- The watermark moves only after the pipeline's last step succeeds, and only on an
  incremental run (a backfill never moves it).
- Window: incremental = [watermark - lookback, now); first run = [now - first_window, now);
  backfill = the explicit --from/--to.
- Every run and step is in the ledger; a heartbeat thread beats while a run is open.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from clickhouse_connect.driver.client import Client as ClickHouseClient
from psycopg_pool import ConnectionPool

from etl.core.alerts import Alerts, OrgFailure
from etl.core.logging import get_logger
from etl.core.model import Mode, Org, Pipeline, RunContext, Status, StepResult, Window
from etl.core.run_ledger import Ledger, error_fields, heartbeat
from etl.core.settings import Settings

log = get_logger(__name__)
Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


def plan_window(
    pipeline: Pipeline,
    watermark: datetime | None,
    now: datetime,
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[Window, Mode]:
    if start is not None or end is not None:
        stop = end or now
        return Window(start or stop - pipeline.first_window, stop), Mode.BACKFILL
    begin = watermark - pipeline.lookback if watermark else now - pipeline.first_window
    return Window(min(begin, now), now), Mode.INCREMENTAL


@dataclass(frozen=True, slots=True)
class OrgOutcome:
    org_id: str
    job_id: str
    status: Status
    failed_step: str | None = None
    error_type: str | None = None
    message: str | None = None


@dataclass(slots=True)
class RunSummary:
    pipeline: str
    outcomes: list[OrgOutcome] = field(default_factory=list[OrgOutcome])

    @property
    def failed(self) -> list[OrgOutcome]:
        return [o for o in self.outcomes if o.status is not Status.SUCCEEDED]

    @property
    def ok(self) -> bool:
        return not self.failed


@dataclass(slots=True)
class Runner:
    settings: Settings
    pg: ConnectionPool
    ch: ClickHouseClient
    ledger: Ledger
    alerts: Alerts
    clock: Clock = utc_now
    attempt: int = 1

    def run(self, pipeline: Pipeline, orgs: Sequence[Org], *, start: datetime | None = None,
            end: datetime | None = None) -> RunSummary:  # fmt: skip
        summary = RunSummary(pipeline.name)
        for org in orgs:
            summary.outcomes.append(self.run_org(pipeline, org, start=start, end=end))
        if summary.failed:
            failures = [OrgFailure(o.org_id, o.job_id, o.failed_step or "", o.error_type or "",
                                   o.message or "") for o in summary.failed]  # fmt: skip
            self.alerts.pipeline_failed(pipeline.name, failures, total=len(orgs))
        return summary

    def run_org(self, pipeline: Pipeline, org: Org, *, start: datetime | None = None,
                end: datetime | None = None) -> OrgOutcome:  # fmt: skip
        now = self.clock()
        watermark = self.ledger.watermark(org.org_id, pipeline.source, pipeline.name)
        window, mode = plan_window(pipeline, watermark, now, start, end)
        job_id = self.ledger.start_run(
            org_id=org.org_id, source=pipeline.source, pipeline=pipeline.name, mode=mode,
            window=window, attempt=self.attempt, execution=self.settings.execution,
            task_index=self.settings.task_index,
        )  # fmt: skip
        bound = log.bind(pipeline=pipeline.name, org_id=org.org_id, job_id=job_id, mode=mode.value)
        ctx = RunContext(self.settings, self.pg, self.ch, pipeline.name, job_id, mode, bound, now)
        bound.info("etl.run_started", window_start=window.start.isoformat(),
                   window_end=window.end.isoformat())  # fmt: skip
        with heartbeat(self.ledger, job_id, self.settings.run.heartbeat_s):
            return self._steps(ctx, pipeline, org, window, mode)

    def _steps(self, ctx: RunContext, pipeline: Pipeline, org: Org, window: Window,
               mode: Mode) -> OrgOutcome:  # fmt: skip
        totals = StepResult()
        details: dict[str, object] = {}
        for ordinal, step in enumerate(pipeline.steps):
            step_id = self.ledger.start_step(ctx.job_id, ordinal, step.name)
            try:
                result = step.run(ctx, org, window)
            except Exception as exc:  # noqa: BLE001 - one org's failure must not stop the others
                return self._fail(ctx, org, step_id, step.name, exc, totals, Status.FAILED)
            except BaseException as exc:  # SIGTERM/Ctrl-C: record, then stop the process
                self._fail(ctx, org, step_id, step.name, exc, totals, Status.KILLED)
                raise
            self.ledger.finish_step(step_id, Status.SUCCEEDED, result=result)
            details[step.name] = dict(result.detail)
            totals = StepResult(totals.records_in + result.records_in,
                                totals.records_out + result.records_out, details)  # fmt: skip
        if mode is Mode.INCREMENTAL:
            self.ledger.advance_watermark(org.org_id, pipeline.source, pipeline.name, window.end,
                                          mode)  # fmt: skip
        self.ledger.finish_run(ctx.job_id, Status.SUCCEEDED, totals=totals)
        ctx.log.info("etl.run_succeeded", records_in=totals.records_in,
                     records_out=totals.records_out)  # fmt: skip
        return OrgOutcome(org.org_id, ctx.job_id, Status.SUCCEEDED)

    def _fail(self, ctx: RunContext, org: Org, step_id: int, step: str, exc: BaseException,
              totals: StepResult, status: Status) -> OrgOutcome:  # fmt: skip
        self.ledger.finish_step(step_id, status, error=exc)
        self.ledger.finish_run(ctx.job_id, status, totals=totals, error=exc)
        error_type, message = error_fields(exc)
        ctx.log.error("etl.run_failed", step=step, error_type=error_type, status=status.value)
        return OrgOutcome(org.org_id, ctx.job_id, status, step, error_type, message)
