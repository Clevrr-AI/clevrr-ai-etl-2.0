"""The step model (plan 7.2): a pipeline is an ordered list of steps; a step is one
idempotent unit `step(ctx, org, window) -> StepResult` (extract, stage raw, load Postgres,
sync ClickHouse, check). A step reads no environment and keeps no module state: what it needs
comes in `ctx`, so it is testable on its own.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from clickhouse_connect.driver.client import Client as ClickHouseClient
    from psycopg_pool import ConnectionPool
    from structlog.stdlib import BoundLogger

    from etl.core.settings import Settings


class Mode(StrEnum):
    INCREMENTAL = "incremental"  # from the watermark (minus the lookback) to now
    BACKFILL = "backfill"  # an explicit --from/--to; never moves the watermark


class Status(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    KILLED = "killed"  # the process died or was stopped; the sweeper also sets it


@dataclass(frozen=True, slots=True)
class Window:
    """[start, end) in UTC."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("A window needs time-zone-aware datetimes.")
        if self.start > self.end:
            raise ValueError("A window starts before it ends.")

    @property
    def days(self) -> float:
        return (self.end - self.start) / timedelta(days=1)


@dataclass(frozen=True, slots=True)
class Org:
    """One org's enabled connector for a source."""

    org_id: str
    connector_id: str
    source: str
    # Provider credentials. Never logged, never put in a repr.
    credentials: Mapping[str, Any] = field(repr=False, default_factory=dict[str, Any])
    config: Mapping[str, Any] = field(default_factory=dict[str, Any])
    historic_done: bool = False


@dataclass(frozen=True, slots=True)
class StepResult:
    records_in: int = 0
    records_out: int = 0
    detail: Mapping[str, Any] = field(default_factory=dict[str, Any])


class StepFailed(Exception):
    """A step's own verdict that its work is wrong or incomplete (a page cap reached, a
    check failed, a missing token). The org's run fails; the other orgs go on."""

    def __init__(self, reason: str, *, detail: Mapping[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = dict(detail or {})


@dataclass(frozen=True, slots=True)
class RunContext:
    settings: Settings
    pg: ConnectionPool
    ch: ClickHouseClient
    pipeline: str
    job_id: str
    mode: Mode
    log: BoundLogger
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))


StepFn = Callable[[RunContext, Org, Window], StepResult]


@dataclass(frozen=True, slots=True)
class Step:
    name: str
    run: StepFn


@dataclass(frozen=True, slots=True)
class Pipeline:
    name: str  # e.g. "shopify-sales"; also the watermark entity
    source: str  # organization_connectors.connector_name, e.g. "shopify"
    steps: tuple[Step, ...]
    # Re-read this far behind the watermark each run (late conversions, edited orders).
    lookback: timedelta
    # The window of an org's first run (no watermark yet).
    first_window: timedelta
    # Cloud Run task timeout and retries (infra/); the runner does not enforce them.
    timeout: timedelta = timedelta(hours=1)
    retries: int = 1
    schedule: str = ""  # cron hint for infra/, UTC

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValueError(f"Pipeline {self.name} has no steps.")
        names = [s.name for s in self.steps]
        if len(names) != len(set(names)):
            raise ValueError(f"Pipeline {self.name} repeats a step name.")
