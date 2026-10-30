"""The run ledger (plan 7.1): `etl_runs` (one row per pipeline x org run), `etl_steps` (one row
per step of a run) and `etl_watermarks`, in the target schema.

A running run beats `heartbeat_at` every `run.heartbeat_s`; the sweeper marks a `running` run
whose heartbeat is older than `run.stale_after_s` as `killed`, so a process that died never
leaves a row `running` forever (legacy did).
"""

from __future__ import annotations

import threading
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Protocol

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from etl.core.model import Mode, Status, StepResult, Window

ERROR_TEXT_MAX = 2000

_START_RUN = """
INSERT INTO etl_runs (org_id, connector_key, pipeline, mode, window_start, window_end, status,
    attempt, execution, task_index, heartbeat_at)
VALUES (%(org_id)s, %(source)s, %(pipeline)s, %(mode)s, %(start)s, %(end)s, 'running',
    %(attempt)s, %(execution)s, %(task_index)s, now())
RETURNING job_id
"""
_FINISH_RUN = """
UPDATE etl_runs SET status = %(status)s, records_in = %(records_in)s,
    records_out = %(records_out)s, error_type = %(error_type)s, error_message = %(error_message)s,
    summary_json = %(summary)s, finished_at = now(), updated_at = now()
WHERE job_id = %(job_id)s
"""
_HEARTBEAT = (
    "UPDATE etl_runs SET heartbeat_at = now() WHERE job_id = %(job_id)s AND status = 'running'"
)
_START_STEP = """
INSERT INTO etl_steps (job_id, ordinal, name, status, started_at)
VALUES (%(job_id)s, %(ordinal)s, %(name)s, 'running', now())
RETURNING step_id
"""
_FINISH_STEP = """
UPDATE etl_steps SET status = %(status)s, finished_at = now(), records_in = %(records_in)s,
    records_out = %(records_out)s, detail = %(detail)s, error_type = %(error_type)s,
    error_message = %(error_message)s
WHERE step_id = %(step_id)s
"""
_WATERMARK = """
SELECT last_high_watermark FROM etl_watermarks
WHERE org_id = %(org_id)s AND connector_key = %(source)s AND entity = %(entity)s
"""
_LAST_RUN = """
SELECT last_run_at FROM etl_watermarks
WHERE org_id = %(org_id)s AND connector_key = %(source)s AND entity = %(entity)s
"""
_ADVANCE = """
INSERT INTO etl_watermarks (org_id, connector_key, entity, last_high_watermark, last_run_at, mode)
VALUES (%(org_id)s, %(source)s, %(entity)s, %(at)s, now(), %(mode)s)
ON CONFLICT (org_id, connector_key, entity) DO UPDATE
SET last_high_watermark = GREATEST(etl_watermarks.last_high_watermark,
                                   EXCLUDED.last_high_watermark),
    last_run_at = now(), mode = EXCLUDED.mode
"""
_SWEEP = """
UPDATE etl_runs SET status = 'killed', error_type = 'stale_heartbeat',
    error_message = 'No heartbeat for ' || %(stale_after_s)s
        || ' s: the process died or was stopped.',
    finished_at = now(), updated_at = now()
WHERE status = 'running' AND heartbeat_at < now() - make_interval(secs => %(stale_after_s)s)
RETURNING job_id
"""
_SWEEP_STEPS = """
UPDATE etl_steps SET status = 'killed', finished_at = now()
WHERE status = 'running' AND job_id = ANY(%(job_ids)s)
"""


def _first(row: tuple[Any, ...] | None) -> Any:
    if row is None:
        raise RuntimeError("An INSERT ... RETURNING returned no row.")
    return row[0]


def error_fields(error: BaseException | None) -> tuple[str | None, str | None]:
    """The exception's type and a short message. Steps must not put credentials in messages."""
    if error is None:
        return None, None
    return type(error).__name__, str(error)[:ERROR_TEXT_MAX]


class Ledger(Protocol):
    def start_run(self, *, org_id: str, source: str, pipeline: str, mode: Mode, window: Window,
                  attempt: int, execution: str | None, task_index: int) -> str: ...  # fmt: skip
    def finish_run(self, job_id: str, status: Status, *, totals: StepResult,
                   error: BaseException | None = None) -> None: ...  # fmt: skip
    def heartbeat(self, job_id: str) -> None: ...
    def start_step(self, job_id: str, ordinal: int, name: str) -> int: ...
    def finish_step(self, step_id: int, status: Status, *, result: StepResult | None = None,
                    error: BaseException | None = None) -> None: ...  # fmt: skip
    def watermark(self, org_id: str, source: str, entity: str) -> datetime | None: ...
    def last_run_at(self, org_id: str, source: str, entity: str) -> datetime | None: ...
    def advance_watermark(self, org_id: str, source: str, entity: str, at: datetime,
                          mode: Mode) -> None: ...  # fmt: skip


class PgLedger:
    """The ledger in Postgres. Every call uses its own short transaction, so a step that
    fails or hangs never holds a ledger row's lock."""

    def __init__(self, pool: ConnectionPool) -> None:
        self.pool = pool

    def start_run(self, *, org_id: str, source: str, pipeline: str, mode: Mode, window: Window,
                  attempt: int, execution: str | None, task_index: int) -> str:  # fmt: skip
        params = {"org_id": org_id, "source": source, "pipeline": pipeline, "mode": mode.value,
                  "start": window.start, "end": window.end, "attempt": attempt,
                  "execution": execution, "task_index": task_index}  # fmt: skip
        with self.pool.connection() as conn:
            row = conn.execute(_START_RUN, params).fetchone()
        return str(_first(row))

    def finish_run(self, job_id: str, status: Status, *, totals: StepResult,
                   error: BaseException | None = None) -> None:  # fmt: skip
        error_type, message = error_fields(error)
        params = {"job_id": job_id, "status": status.value, "records_in": totals.records_in,
                  "records_out": totals.records_out, "error_type": error_type,
                  "error_message": message, "summary": Jsonb(dict(totals.detail))}  # fmt: skip
        with self.pool.connection() as conn:
            conn.execute(_FINISH_RUN, params)

    def heartbeat(self, job_id: str) -> None:
        with self.pool.connection() as conn:
            conn.execute(_HEARTBEAT, {"job_id": job_id})

    def start_step(self, job_id: str, ordinal: int, name: str) -> int:
        with self.pool.connection() as conn:
            row = conn.execute(
                _START_STEP, {"job_id": job_id, "ordinal": ordinal, "name": name}
            ).fetchone()
        return int(_first(row))

    def finish_step(self, step_id: int, status: Status, *, result: StepResult | None = None,
                    error: BaseException | None = None) -> None:  # fmt: skip
        result = result or StepResult()
        error_type, message = error_fields(error)
        params = {"step_id": step_id, "status": status.value, "records_in": result.records_in,
                  "records_out": result.records_out, "detail": Jsonb(dict(result.detail)),
                  "error_type": error_type, "error_message": message}  # fmt: skip
        with self.pool.connection() as conn:
            conn.execute(_FINISH_STEP, params)

    def watermark(self, org_id: str, source: str, entity: str) -> datetime | None:
        params = {"org_id": org_id, "source": source, "entity": entity}
        with self.pool.connection() as conn:
            row = conn.execute(_WATERMARK, params).fetchone()
        return None if row is None else row[0]

    def last_run_at(self, org_id: str, source: str, entity: str) -> datetime | None:
        """When the org's last successful incremental run of the pipeline advanced the
        watermark; None before the first (etl tick's dueness, etl/core/schedules.py)."""
        params = {"org_id": org_id, "source": source, "entity": entity}
        with self.pool.connection() as conn:
            row = conn.execute(_LAST_RUN, params).fetchone()
        return None if row is None else row[0]

    def advance_watermark(self, org_id: str, source: str, entity: str, at: datetime,
                          mode: Mode) -> None:  # fmt: skip
        """Never moves a watermark backwards (GREATEST)."""
        params = {"org_id": org_id, "source": source, "entity": entity, "at": at,
                  "mode": mode.value}  # fmt: skip
        with self.pool.connection() as conn:
            conn.execute(_ADVANCE, params)

    def sweep(self, stale_after_s: int) -> list[str]:
        """Mark stale `running` runs (and their running steps) `killed`. Returns their ids."""
        with self.pool.connection() as conn:
            killed = [str(r[0]) for r in conn.execute(_SWEEP, {"stale_after_s": stale_after_s})]
            if killed:
                conn.execute(_SWEEP_STEPS, {"job_ids": killed})
        return killed


@contextmanager
def heartbeat(ledger: Ledger, job_id: str, every_s: float) -> Generator[None]:
    """Beat `job_id` every `every_s` seconds on a daemon thread while the block runs. A failed
    beat is skipped: the next one retries, and the sweeper's margin is many beats."""
    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(every_s):
            try:
                ledger.heartbeat(job_id)
            except Exception:  # noqa: BLE001, S112 - a lost beat must not kill the run
                continue

    thread = threading.Thread(target=beat, name=f"heartbeat-{job_id}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=every_s)
