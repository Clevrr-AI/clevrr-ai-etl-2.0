# Graph API payloads are untyped JSON; these modules read them as legacy did.
# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false
"""Meta Graph API client: GETs with legacy's retry rules, cursor paging, and the insights
pull split by month (legacy fetchers/meta_ads_fetcher.py, meta_ads_creative_fetcher.py,
meta_ads_structure_fetcher.py, meta_ads_region_fetcher.py).

Legacy sends the access token in the query string, and so does this client. The token is
therefore in every request URL: no URL is logged (httpx logs are off) and every error raised
here carries the HTTP status and the Graph error code only.

Retries, 8 attempts (legacy): HTTP 429/5xx wait 2^n s up to 60 s (or Retry-After). HTTP
400/403 with a rate-limit code wait 10*(n+1) s up to 180 s; with a transient insights subcode,
2^n s. Graph codes 190 and 102 (dead token) and 10, 200-299 (no permission) fail at once.

"Please reduce the amount of data" (HTTP 500, Graph code 1) is never retried: the same
synchronous query fails again (blissclub.in 2026-10-20..22: 8 of 8, and at limit=100). The
ad-level insights then run the range as an async report job (POST /insights, poll the
report_run_id, read /{id}/insights), as legacy does since 653b6ff. Each range's rows are
buffered and handed over only when the whole range succeeded, and a one-day range is never
"split" (legacy's halves collapsed to empty ranges and the day went missing without an error).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator, Mapping
from datetime import date, timedelta
from typing import Any

import httpx

from etl.core.logging import get_logger
from etl.core.model import StepFailed

log = get_logger(__name__)

ATTEMPTS = 8
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
RATE_LIMIT_CODES = frozenset({4, 17, 32, 80000, 80004})
RATE_LIMIT_SUBCODES = frozenset({1504022, 1504039, 2446079})
TRANSIENT_SUBCODES = frozenset({1504044, 1504018})  # "Unknown error", "Request timed out"
TOKEN_CODES = frozenset({190, 102})
MAX_MONTHS_BACK = 37
ASYNC_POLL_S = 10.0  # legacy META_ADS_ASYNC_POLL_SECONDS
ASYNC_TIMEOUT_S = 1800.0  # legacy META_ADS_ASYNC_TIMEOUT_SECONDS
ASYNC_DONE = "Job Completed"
ASYNC_DEAD = frozenset({"Job Failed", "Job Skipped"})


class GraphError(StepFailed):
    def __init__(self, reason: str, status: int, code: Any = None) -> None:
        super().__init__(reason, detail={"status": status, "graph_code": code})
        self.status = status


class ReduceDataError(GraphError):
    """Meta refused a synchronous insights query as too heavy (HTTP 500, code 1)."""


def _error(body: Any) -> dict[str, Any]:
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return body["error"]
    return {}


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _retry_after(response: httpx.Response) -> int | None:
    return _int(response.headers.get("Retry-After"))


def _is_reduce_data(response: httpx.Response) -> bool:
    """Legacy _is_reduce_data_error: code 1 and "reduce the amount of data" in the message."""
    err = _error(_json(response))
    return (
        str(err.get("code")) == "1"
        and "reduce the amount of data" in str(err.get("message") or "").lower()
    )


class Graph:
    def __init__(self, base_url: str, token: str, http: httpx.Client, *,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:  # fmt: skip
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.http = http
        self.sleep = sleep
        self.clock = clock

    def get(self, path: str, params: Mapping[str, Any], *, token: str | None = None) -> Any:
        return self._call("GET", path, params, token)

    def post(self, path: str, params: Mapping[str, Any]) -> Any:
        """A form POST (legacy's async report create), with the GET retry rules."""
        return self._call("POST", path, params, None)

    def _call(self, method: str, path: str, params: Mapping[str, Any],
              token: str | None) -> Any:  # fmt: skip
        url = path if path.startswith("https://") else f"{self.base_url}/{path.lstrip('/')}"
        query = {"access_token": token or self.token, **params}
        response: httpx.Response | None = None
        for attempt in range(ATTEMPTS):
            try:
                response = (self.http.post(url, data=query) if method == "POST"
                            else self.http.get(url, params=query))  # fmt: skip
            except httpx.TransportError as exc:
                response = None
                wait, kind = min(2.0**attempt, 60.0), type(exc).__name__
            else:
                wait, kind = self._wait(response, attempt)
            if kind == "ok" and response is not None:
                return self._body(response)
            if attempt + 1 < ATTEMPTS:
                self.sleep(wait)
        status = response.status_code if response is not None else 0
        raise GraphError(f"Meta Graph: HTTP {status} after {ATTEMPTS} attempts", status,
                         _error(_json(response)).get("code"))  # fmt: skip

    def _wait(self, response: httpx.Response, attempt: int) -> tuple[float, str]:
        """(seconds, "retry") to retry, (0, "ok") to return the body; raises otherwise."""
        status = response.status_code
        if status == httpx.codes.INTERNAL_SERVER_ERROR.value and _is_reduce_data(response):
            raise ReduceDataError("Meta Graph: reduce the amount of data (HTTP 500 code 1)",
                                  status, 1)  # fmt: skip
        if status in RETRY_STATUS:
            return float(_retry_after(response) or min(2**attempt, 60)), "retry"
        if status in (400, 403):
            err = _error(_json(response))
            code, sub = _int(err.get("code")), _int(err.get("error_subcode"))
            _raise_if_auth(code, status)
            if code in RATE_LIMIT_CODES or sub in RATE_LIMIT_SUBCODES:
                return float(_retry_after(response) or min(10 * (attempt + 1), 180)), "retry"
            if sub in TRANSIENT_SUBCODES:
                return float(_retry_after(response) or min(2**attempt, 60)), "retry"
        if status != httpx.codes.OK.value:
            err = _error(_json(response))
            raise GraphError(f"Meta Graph: HTTP {status} code {err.get('code')}", status,
                             err.get("code"))  # fmt: skip
        return 0.0, "ok"

    def _body(self, response: httpx.Response) -> Any:
        body = _json(response)
        err = _error(body)
        if err:
            code = _int(err.get("code"))
            _raise_if_auth(code, response.status_code)
            raise GraphError(f"Meta Graph error code {code}", response.status_code, code)
        self._pace(response)
        return body

    def _pace(self, response: httpx.Response) -> None:
        """Legacy: slow down when the ad account's usage header is at 90% or more."""
        try:
            usage = json.loads(response.headers.get("x-ad-account-usage") or "{}")
        except ValueError:
            return
        pct = usage.get("acc_id_util_pct") or usage.get("acc_id_util_pct_daily")
        if isinstance(pct, int | float) and pct >= 90:
            self.sleep(2)

    def paged(self, path: str, params: Mapping[str, Any], *, stop_on_empty: bool = False,
              pause: float = 0.2) -> Iterator[list[dict[str, Any]]]:  # fmt: skip
        """Every page of a cursor-paged edge (`paging.cursors.after`)."""
        after: str | None = None
        while True:
            body = self.get(path, {**params, **({"after": after} if after else {})})
            data: list[dict[str, Any]] = list(body.get("data") or [])
            yield data
            after = ((body.get("paging") or {}).get("cursors") or {}).get("after")
            if not after or (stop_on_empty and not data):
                return
            self.sleep(pause)


def _json(response: httpx.Response | None) -> Any:
    if response is None:
        return None
    try:
        return response.json()
    except ValueError:
        return None


def _raise_if_auth(code: int | None, status: int) -> None:
    if code in TOKEN_CODES:
        raise GraphError(f"Meta token rejected (Graph code {code}): reconnect Meta", status, code)
    if code is not None and (code == 10 or 200 <= code <= 299):
        raise GraphError(f"Meta permission denied (Graph code {code})", status, code)


def month_chunks(start: date, end: date) -> list[tuple[date, date]]:
    """[start, end) cut at month starts (legacy month_chunks)."""
    chunks: list[tuple[date, date]] = []
    cur = date(start.year, start.month, 1)
    while cur < end:
        nxt = date(cur.year + (cur.month // 12), cur.month % 12 + 1, 1)
        since, until = max(cur, start), min(nxt, end)
        if since < until:
            chunks.append((since, until))
        cur = nxt
    return chunks


def earliest_allowed(today: date) -> date:
    """Meta serves at most 37 months back (legacy clamped to the month start)."""
    months = today.year * 12 + today.month - 1 - MAX_MONTHS_BACK
    return date(months // 12, months % 12 + 1, 1)


def insights(graph: Graph, account: str, start: date, end: date, *, fields: str, level: str,
             today: date, extra: Mapping[str, Any],
             async_fallback: bool = False) -> Iterator[list[dict[str, Any]]]:  # fmt: skip
    """Daily insights rows for [start, end), month by month, one list per range once the whole
    range succeeded. A range of 2+ days that fails with HTTP 500 is fetched again in two halves
    (legacy); a one-day range is not split. With `async_fallback` (legacy's ad-level fetcher),
    a "reduce the amount of data" refusal runs the range as an async report job instead."""
    start = max(start, earliest_allowed(today))
    for since, until in month_chunks(start, end):
        try:
            yield _rows(graph, account, since, until, fields, level, extra, async_fallback)
        except GraphError as exc:
            if exc.status != httpx.codes.INTERNAL_SERVER_ERROR.value or (until - since).days < 2:
                raise
            mid = since + timedelta(days=(until - since).days // 2)
            log.warning("meta.split_retry", account=account, since=since.isoformat(),
                        mid=mid.isoformat(), until=until.isoformat())  # fmt: skip
            yield _rows(graph, account, since, mid, fields, level, extra, async_fallback)
            yield _rows(graph, account, mid, until, fields, level, extra, async_fallback)


def _rows(graph: Graph, account: str, since: date, until: date, fields: str, level: str,
          extra: Mapping[str, Any], async_fallback: bool) -> list[dict[str, Any]]:  # fmt: skip
    """The range's rows, buffered: a page that fails part-way leaves nothing behind."""
    try:
        return [r for page in _range(graph, account, since, until, fields, level, extra)
                for r in page]  # fmt: skip
    except ReduceDataError:
        if not async_fallback:
            raise
        log.warning("meta.reduce_data_async_fallback", account=account,
                    since=since.isoformat(), until=until.isoformat())  # fmt: skip
        return _range_async(graph, account, since, until, fields, level, extra)


def _range(graph: Graph, account: str, since: date, until: date, fields: str, level: str,
           extra: Mapping[str, Any]) -> Iterator[list[dict[str, Any]]]:  # fmt: skip
    last = until - timedelta(days=1)  # Graph's until is inclusive
    if since > last:
        return
    window = json.dumps({"since": since.isoformat(), "until": last.isoformat()},
                        separators=(",", ":"))  # fmt: skip
    params = {"level": level, "time_increment": 1, "time_range": window, "fields": fields,
              "limit": 500, **extra}  # fmt: skip
    yield from graph.paged(f"{account}/insights", params)


def _window(since: date, until: date) -> str:
    last = until - timedelta(days=1)  # Graph's until is inclusive
    return json.dumps({"since": since.isoformat(), "until": last.isoformat()},
                      separators=(",", ":"))  # fmt: skip


def _range_async(graph: Graph, account: str, since: date, until: date, fields: str, level: str,
                 extra: Mapping[str, Any]) -> list[dict[str, Any]]:  # fmt: skip
    """The same query as an async report job (legacy fetch_range_async): create, poll until
    "Job Completed" at 100 %, then read every page of /{report_run_id}/insights."""
    params = {"level": level, "time_increment": 1, "time_range": _window(since, until),
              "fields": fields, **extra}  # fmt: skip
    run_id = str((graph.post(f"{account}/insights", params) or {}).get("report_run_id") or "")
    if not run_id:
        raise GraphError("Meta async report: the create call returned no report_run_id", 0)
    deadline = graph.clock() + ASYNC_TIMEOUT_S
    while True:
        state = graph.get(run_id, {"fields": "async_status,async_percent_completion"}) or {}
        status, pct = state.get("async_status"), _int(state.get("async_percent_completion"))
        if status == ASYNC_DONE and (pct is None or pct >= 100):
            break
        if status in ASYNC_DEAD:
            raise GraphError(f"Meta async report ended with status {status!r}", 0)
        if graph.clock() > deadline:
            raise GraphError(f"Meta async report not done after {ASYNC_TIMEOUT_S:.0f} s "
                             f"(status {status!r}, {pct} %)", 0)  # fmt: skip
        graph.sleep(ASYNC_POLL_S)
    rows: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        body = graph.get(f"{run_id}/insights",
                         {"limit": 500, **({"after": after} if after else {})}) or {}  # fmt: skip
        data = list(body.get("data") or [])
        rows.extend(data)
        paging = body.get("paging") or {}
        after = (paging.get("cursors") or {}).get("after")
        if not data or not after or not paging.get("next"):
            log.info("meta.async_report_read", account=account, rows=len(rows))
            return rows
        graph.sleep(0.2)
