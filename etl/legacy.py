"""Run from legacy's CronJob settings (`etl legacy`), so the existing `etl-all-*` CronJobs in the
cluster run the v2 image unchanged: their ConfigMap `etl-config`, Secret `etl-secrets` and
`ETL_*` variables stay as they are, and only the image tag moves.

- Legacy connection names become the ETL's settings names when the v2 name is not set:
  POSTGRES_HOST -> POSTGRES__HOST, CKH_HOST -> CLICKHOUSE__HOST, CLICKHOUSE_CLUSTER ->
  CLICKHOUSE__CLUSTER, ... The proxy runs in the image (entrypoint.sh), as legacy's did.
- `ETL_COMMAND` plus `ETL_SCOPES` / `ETL_GROUP_PHASES` / `ETL_CONNECTORS` / `ETL_ORG_IDS` /
  `ETL_START_DATE` / `ETL_END_DATE` become one or more `etl run ...` invocations (`plan`).
- Parity guard: until `ETL_V2_CUTOVER=true`, a run whose targets are legacy's own tables
  (`public`, `clevrr_db`) is refused, so a job with no target settings cannot double-write.

This module reads the environment, like etl/core/settings.py: it is the legacy boundary.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

from etl.core.model import StepFailed

LEGACY_TO_V2: Final[dict[str, str]] = {
    "POSTGRES_HOST": "POSTGRES__HOST",
    "POSTGRES_PORT": "POSTGRES__PORT",
    "POSTGRES_DB": "POSTGRES__DB",
    "POSTGRES_USER": "POSTGRES__USER",
    "POSTGRES_PASSWORD": "POSTGRES__PASSWORD",
    "CKH_HOST": "CLICKHOUSE__HOST",
    "CKH_PORT": "CLICKHOUSE__PORT",
    "CKH_USER": "CLICKHOUSE__USER",
    "CKH_PASSWORD": "CLICKHOUSE__PASSWORD",
    "CLICKHOUSE_CLUSTER": "CLICKHOUSE__CLUSTER",
    # The second spelling some jobs use (ckh-etl-ondemand, the reliability report). CKH_* wins
    # when both are set, as in legacy's code.
    "CLICKHOUSE_HOST": "CLICKHOUSE__HOST",
    "CLICKHOUSE_PORT": "CLICKHOUSE__PORT",
    "CLICKHOUSE_USER": "CLICKHOUSE__USER",
    "CLICKHOUSE_PASSWORD": "CLICKHOUSE__PASSWORD",
    "CLICKHOUSE_DB": "CLICKHOUSE__DATABASE",
}
NOT_PORTED: Final[dict[str, str]] = {
    "delhivery": "Delhivery is parked (decision 2026-10-19); legacy keeps its CronJob.",
    "amazon_ads": "Amazon Ads is not ported (decision 2026-10-17).",
}
TRUE: Final = frozenset({"1", "true", "yes"})


class NotPorted(StepFailed):
    """A legacy job v2 has no pipeline for; the run stops with a clear message."""


def translate_env(env: Mapping[str, str]) -> dict[str, str]:
    """The v2 settings names legacy's names stand for; a v2 name already set wins."""
    out: dict[str, str] = {}
    for (
        legacy,
        v2,
    ) in LEGACY_TO_V2.items():  # first spelling listed wins (CKH_* before CLICKHOUSE_*)
        if legacy in env and v2 not in env and v2 not in out:
            out[v2] = env[legacy]
    return out


def _csv(value: str | None) -> list[str]:
    return [s.strip() for s in (value or "").split(",") if s.strip()]


def pipelines_for(connector: str, scopes: Sequence[str], phases: Sequence[str]) -> list[str]:
    """The v2 pipelines one legacy job ran (legacy ETL/etl_registry.py groups)."""
    if connector in NOT_PORTED:
        raise NotPorted(NOT_PORTED[connector])
    if connector == "shopify":
        return ["shopify-catalog"] if "catalog" in scopes else ["shopify-sales"]
    if connector == "meta_ads":
        return ["meta-ads", "meta-ads-region"]  # legacy's job pulled the region breakdown too
    if connector == "google_ads":
        return ["google-ads"]
    if connector == "internal" and any(p.startswith("costs") for p in phases):
        return ["costs"]
    raise NotPorted(f"No v2 pipeline for legacy connector {connector!r}.")


def _end_date(end: str) -> str:
    """`--to` is an exclusive midnight. Legacy's on-demand trigger sends `T-1 23:59:59` to mean
    the whole of T-1 (its exclusive_day_end), so an end inside a day becomes the next day."""
    day = end[:10]
    if len(end) > 10 and end[10:].strip() not in ("", "00:00:00", "00:00"):
        return (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    return day


def _window(env: Mapping[str, str], pipeline: str) -> list[str]:
    """`--from/--to` from ETL_START_DATE/ETL_END_DATE (date part; a midnight end is
    exclusive in both). The costs mirror takes no window (legacy's 2015-2099 was a full pass)."""
    start, end = env.get("ETL_START_DATE", "").strip(), env.get("ETL_END_DATE", "").strip()
    if pipeline == "costs" or not (start and end):
        return []
    return ["--from", start[:10], "--to", _end_date(end)]


def plan(env: Mapping[str, str]) -> list[list[str]]:
    """The `etl` invocations for one legacy job's settings."""
    command = env.get("ETL_COMMAND", "").split()
    if command[:1] == ["run-all-for-connector"] and len(command) > 1:
        connectors = [command[1]]
    elif command[:1] in (["run-from-env"], []):
        connectors = _csv(env.get("ETL_CONNECTORS"))
    else:
        raise NotPorted(f"Legacy command {command[0]!r} has no v2 equivalent.")
    if not connectors:
        raise NotPorted("No connector: set ETL_COMMAND or ETL_CONNECTORS.")
    scopes, phases = _csv(env.get("ETL_SCOPES")), _csv(env.get("ETL_GROUP_PHASES"))
    orgs = [arg for org in _csv(env.get("ETL_ORG_IDS")) for arg in ("--org", org)]
    runs: list[list[str]] = []
    for connector in connectors:
        for pipeline in pipelines_for(connector, scopes, phases):
            runs.append(["run", pipeline, *orgs, *_window(env, pipeline)])
    return runs


def parity_guard(env: Mapping[str, str]) -> None:
    """Refuse legacy's tables as targets until cut-over is declared."""
    if env.get("ETL_V2_CUTOVER", "").lower() in TRUE:
        return
    schema = env.get("POSTGRES__TARGET_SCHEMA", "public")
    database = env.get("CLICKHOUSE__DATABASE", "clevrr_db")
    if schema == "public" or database == "clevrr_db":
        raise SystemExit(
            "Refusing to run: the targets are legacy's tables"
            f" (schema {schema!r}, database {database!r}). During parity set"
            " POSTGRES__TARGET_SCHEMA and CLICKHOUSE__DATABASE (ConfigMap etl-config);"
            " at cut-over set ETL_V2_CUTOVER=true."
        )


@dataclass(frozen=True, slots=True)
class Job:
    """One legacy job, read from the process environment."""

    name: str | None
    runs: list[list[str]]
    translated: list[str]
    log_level: str


def read_job() -> Job:
    """Translate the names into the process environment (for load_settings()), check the
    parity guard, and plan the runs. The only place that reads os.environ for `etl legacy`."""
    translated = translate_env(os.environ)
    os.environ.update(translated)
    parity_guard(os.environ)
    return Job(os.environ.get("ETL_JOB_NAME"), plan(os.environ), sorted(translated),
               os.environ.get("LOG_LEVEL", "INFO"))  # fmt: skip
