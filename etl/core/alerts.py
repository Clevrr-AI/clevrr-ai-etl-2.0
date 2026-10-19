"""Failure alerts. One message per pipeline run that has failed orgs, not one per org.

Slack incoming webhook when `ALERTS__SLACK_WEBHOOK_URL` is set; otherwise the alert is only
logged. An alert never fails the run: a broken webhook is logged and dropped.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx

from etl.core.logging import get_logger
from etl.core.settings import AlertSettings

log = get_logger(__name__)
MAX_LISTED = 20


@dataclass(frozen=True, slots=True)
class OrgFailure:
    org_id: str
    job_id: str
    step: str
    error_type: str
    message: str


class Alerts(Protocol):
    def pipeline_failed(
        self, pipeline: str, failures: Sequence[OrgFailure], *, total: int
    ) -> None: ...


def render(pipeline: str, failures: Sequence[OrgFailure], *, total: int) -> str:
    lines = [f":rotating_light: ETL v2 `{pipeline}`: {len(failures)} of {total} orgs failed."]
    for f in failures[:MAX_LISTED]:
        lines.append(
            f"- `{f.org_id}` at `{f.step}`: {f.error_type}: {f.message[:200]} (run {f.job_id})"
        )
    if len(failures) > MAX_LISTED:
        lines.append(f"... and {len(failures) - MAX_LISTED} more (etl_runs, status failed).")
    return "\n".join(lines)


class LogAlerts:
    def pipeline_failed(self, pipeline: str, failures: Sequence[OrgFailure], *, total: int) -> None:
        log.error("etl.pipeline_failed", pipeline=pipeline, failed=len(failures), total=total,
                  orgs=[f.org_id for f in failures])  # fmt: skip


class SlackAlerts:
    def __init__(self, webhook_url: str, http: httpx.Client) -> None:
        self._url = webhook_url
        self._http = http

    def pipeline_failed(self, pipeline: str, failures: Sequence[OrgFailure], *, total: int) -> None:
        LogAlerts().pipeline_failed(pipeline, failures, total=total)
        try:
            response = self._http.post(
                self._url, json={"text": render(pipeline, failures, total=total)}
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("etl.alert_not_sent", pipeline=pipeline, error=type(exc).__name__)


def build_alerts(settings: AlertSettings, http: httpx.Client) -> Alerts:
    url = settings.slack_webhook_url
    return SlackAlerts(url.get_secret_value(), http) if url is not None else LogAlerts()


def post_message(settings: AlertSettings, text: str) -> None:
    """A report message (not a failure): Slack when the webhook is set, else only logged.
    Never raises."""
    log.info("etl.report", text=text[:2000])
    url = settings.slack_webhook_url
    if url is None:
        return
    try:
        with httpx.Client(timeout=20.0) as http:
            http.post(url.get_secret_value(), json={"text": text}).raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("etl.report_not_sent", error=type(exc).__name__)
