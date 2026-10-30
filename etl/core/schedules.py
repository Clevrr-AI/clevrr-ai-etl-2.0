"""Per-org sync schedules (`etl_schedules`, ETL-owned): how often each org's connector runs.

An org's interval for a source is one of ALLOWED_MINUTES (60 min to a day; every value divides
a day). No row, or no `etl_schedules` entry for the source: once a day, as the nightly jobs did.
`enabled = false` pauses the org for that source.

Dueness is slot-based, anchored at IST midnight: the day is cut into interval-sized slots
(for 240 min: 00:00, 04:00, 08:00 ... IST), and an org is due when its last successful run
(`etl_watermarks.last_run_at`) is older than the start of the current slot. So a run that
fires every hour runs each org at the first tick of its slot, a daily org keeps running once
a night, and nothing drifts.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from psycopg import Connection
from psycopg.rows import TupleRow
from psycopg_pool import ConnectionPool

IST = ZoneInfo("Asia/Kolkata")
DAY_MINUTES = 1440
ALLOWED_MINUTES: frozenset[int] = frozenset({60, 120, 180, 240, 360, 480, 720, DAY_MINUTES})
_EVERY = re.compile(r"^\s*(\d+)\s*(m|min|minutes?|h|hr|hours?|d|days?)?\s*$", re.IGNORECASE)
_WORDS = {"hourly": 60, "daily": DAY_MINUTES}

_FOR_SOURCE = """
SELECT org_id, connector_key, interval_minutes, enabled, note, updated_at
FROM etl_schedules WHERE connector_key = %(source)s ORDER BY org_id
"""
_ALL = """
SELECT org_id, connector_key, interval_minutes, enabled, note, updated_at
FROM etl_schedules ORDER BY connector_key, org_id
"""
_SET = """
INSERT INTO etl_schedules (org_id, connector_key, interval_minutes, enabled, note, updated_at)
VALUES (%(org_id)s, %(source)s, %(minutes)s, %(enabled)s, %(note)s, now())
ON CONFLICT (org_id, connector_key) DO UPDATE
SET interval_minutes = EXCLUDED.interval_minutes, enabled = EXCLUDED.enabled,
    note = EXCLUDED.note, updated_at = now()
"""
_UNSET = "DELETE FROM etl_schedules WHERE org_id = %(org_id)s AND connector_key = %(source)s"


@dataclass(frozen=True, slots=True)
class Schedule:
    org_id: str
    source: str
    interval_minutes: int = DAY_MINUTES
    enabled: bool = True
    note: str | None = None
    updated_at: datetime | None = None

    @property
    def every(self) -> str:
        """The interval for people: `4h`, `daily`, `90m`."""
        if self.interval_minutes == DAY_MINUTES:
            return "daily"
        if self.interval_minutes % 60 == 0:
            return f"{self.interval_minutes // 60}h"
        return f"{self.interval_minutes}m"


def parse_every(text: str) -> int:
    """`4h`, `240`, `240m`, `1d`, `daily`, `hourly` -> minutes, one of ALLOWED_MINUTES."""
    word = text.strip().lower()
    if word in _WORDS:
        return _WORDS[word]
    match = _EVERY.match(word)
    if not match:
        raise ValueError(f"Not an interval: {text!r}. Use 60m, 1h, 4h, 6h, 12h or daily.")
    value, unit = int(match.group(1)), (match.group(2) or "m")[0]
    minutes = value * {"m": 1, "h": 60, "d": DAY_MINUTES}[unit]
    if minutes not in ALLOWED_MINUTES:
        allowed = ", ".join(str(m) for m in sorted(ALLOWED_MINUTES))
        raise ValueError(f"{text!r} is {minutes} min; allowed: {allowed} minutes.")
    return minutes


def slot_start(now: datetime, interval_minutes: int) -> datetime:
    """The start (UTC) of the interval slot `now` falls in; slots start at IST midnight."""
    if interval_minutes not in ALLOWED_MINUTES:
        raise ValueError(f"interval_minutes must be one of {sorted(ALLOWED_MINUTES)}.")
    local = now.astimezone(IST)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    step = timedelta(minutes=interval_minutes)
    slots = (local - midnight) // step
    return (midnight + slots * step).astimezone(UTC)


def is_due(last_run_at: datetime | None, now: datetime, interval_minutes: int) -> bool:
    """Due when no successful run yet, or the last one is before the current slot's start."""
    return last_run_at is None or last_run_at < slot_start(now, interval_minutes)


def _row(row: tuple[Any, ...]) -> Schedule:
    org_id, source, minutes, enabled, note, updated_at = row
    return Schedule(str(org_id), str(source), int(minutes), bool(enabled), note, updated_at)


class ScheduleTable:
    def __init__(self, pool: ConnectionPool[Connection[TupleRow]]) -> None:
        self.pool = pool

    def for_source(self, source: str) -> Mapping[str, Schedule]:
        """The org's schedule by org id; orgs with no row take the daily default."""
        with self.pool.connection() as conn:
            rows = conn.execute(_FOR_SOURCE, {"source": source}).fetchall()
        return {str(r[0]): _row(r) for r in rows}

    def all(self) -> list[Schedule]:
        with self.pool.connection() as conn:
            return [_row(r) for r in conn.execute(_ALL).fetchall()]

    def set(self, org_id: str, source: str, interval_minutes: int, *, enabled: bool = True,
            note: str | None = None) -> Schedule:  # fmt: skip
        if interval_minutes not in ALLOWED_MINUTES:
            raise ValueError(f"interval_minutes must be one of {sorted(ALLOWED_MINUTES)}.")
        params = {"org_id": org_id, "source": source, "minutes": interval_minutes,
                  "enabled": enabled, "note": note}  # fmt: skip
        with self.pool.connection() as conn:
            conn.execute(_SET, params)
        return Schedule(org_id, source, interval_minutes, enabled, note)

    def unset(self, org_id: str, source: str) -> bool:
        with self.pool.connection() as conn:
            result = conn.execute(_UNSET, {"org_id": org_id, "source": source})
        return result.rowcount > 0
