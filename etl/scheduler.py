"""`etl tick <source>`: run a source's pipelines for the orgs that are due.

One CronJob per source fires every hour (cronjobs_dev/etl-tick-*.yaml). Each tick reads the
orgs with an enabled connector, their schedules (`etl_schedules`, default daily) and each
pipeline's last successful run, and runs the pipeline for the orgs whose interval slot has
started since that run (etl/core/schedules.py). A daily org therefore still runs once a
night (the first tick after 00:00 IST); a 4-hour org runs at the first tick of every 4-hour
slot; an org that failed is tried again at the next tick of the same slot.

Only the incremental pipelines are scheduled this way. Full pulls (shopify-catalog, costs) and
the reliability reports keep their own daily CronJobs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from etl import pipelines
from etl.core.logging import get_logger
from etl.core.model import Org, Pipeline
from etl.core.run_ledger import Ledger
from etl.core.schedules import DAY_MINUTES, Schedule, is_due
from etl.runner import Runner, RunSummary

log = get_logger(__name__)
SCHEDULED: Final[Mapping[str, tuple[str, ...]]] = {
    "shopify": ("shopify-sales",),
    "meta_ads": ("meta-ads", "meta-ads-region"),
    "google_ads": ("google-ads",),
}


@dataclass(frozen=True, slots=True)
class Decision:
    org_id: str
    pipeline: str
    due: bool
    reason: str  # paused | first run | slot started | ran this slot
    interval_minutes: int
    last_run_at: datetime | None = None


def scheduled_pipelines(source: str) -> list[Pipeline]:
    try:
        names = SCHEDULED[source]
    except KeyError:
        known = ", ".join(sorted(SCHEDULED))
        raise SystemExit(f"No scheduled pipelines for source {source!r}. Known: {known}.") from None
    return [pipelines.get(name) for name in names]


def decide(org: Org, pipeline: Pipeline, schedules: Mapping[str, Schedule], ledger: Ledger,
           now: datetime) -> Decision:  # fmt: skip
    schedule = schedules.get(org.org_id) or Schedule(org.org_id, pipeline.source, DAY_MINUTES)
    last = ledger.last_run_at(org.org_id, pipeline.source, pipeline.name)
    if not schedule.enabled:
        reason, due = "paused", False
    elif last is None:
        reason, due = "first run", True
    elif is_due(last, now, schedule.interval_minutes):
        reason, due = "slot started", True
    else:
        reason, due = "ran this slot", False
    return Decision(org.org_id, pipeline.name, due, reason, schedule.interval_minutes, last)


def tick(source: str, orgs: Sequence[Org], schedules: Mapping[str, Schedule], runner: Runner,
         ledger: Ledger) -> list[RunSummary]:  # fmt: skip
    """Run each scheduled pipeline of `source` for its due orgs; one summary per pipeline
    that had any. Dueness is decided per pipeline, right before it runs."""
    summaries: list[RunSummary] = []
    for pipeline in scheduled_pipelines(source):
        now = runner.clock()
        decisions = [decide(org, pipeline, schedules, ledger, now) for org in orgs]
        due_ids = {d.org_id for d in decisions if d.due}
        log.info("etl.tick", pipeline=pipeline.name, orgs=len(orgs), due=sorted(due_ids),
                 skipped=[f"{d.org_id}:{d.reason}" for d in decisions if not d.due])  # fmt: skip
        due = [o for o in orgs if o.org_id in due_ids]
        if due:
            summaries.append(runner.run(pipeline, due))
    return summaries
