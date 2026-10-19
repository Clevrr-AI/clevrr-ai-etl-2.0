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
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator, Mapping
from datetime import date, timedelta
from typing import Any

import httpx

from etl.core.model import StepFailed

ATTEMPTS = 8
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
RATE_LIMIT_CODES = frozenset({4, 17, 32, 80000, 80004})
RATE_LIMIT_SUBCODES = frozenset({1504022, 1504039, 2446079})
TRANSIENT_SUBCODES = frozenset({1504044, 1504018})  # "Unknown error", "Request timed out"
TOKEN_CODES = frozenset({190, 102})
MAX_MONTHS_BACK = 37


class GraphError(StepFailed):
    def __init__(self, reason: str, status: int, code: Any = None) -> None:
        super().__init__(reason, detail={"status": status, "graph_code": code})
        self.status = status


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


class Graph:
    def __init__(self, base_url: str, token: str, http: httpx.Client, *,
                 sleep: Callable[[float], None] = time.sleep) -> None:  # fmt: skip
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.http = http
        self.sleep = sleep

    def get(self, path: str, params: Mapping[str, Any], *, token: str | None = None) -> Any:
        url = path if path.startswith("https://") else f"{self.base_url}/{path.lstrip('/')}"
        query = {"access_token": token or self.token, **params}
        response: httpx.Response | None = None
        for attempt in range(ATTEMPTS):
            try:
                response = self.http.get(url, params=query)
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
             today: date, extra: Mapping[str, Any]) -> Iterator[list[dict[str, Any]]]:  # fmt: skip
    """Daily insights rows for [start, end), month by month; a month that fails with HTTP 500
    is fetched again in two halves (legacy)."""
    start = max(start, earliest_allowed(today))
    for since, until in month_chunks(start, end):
        try:
            yield from _range(graph, account, since, until, fields, level, extra)
        except GraphError as exc:
            if exc.status != 500:
                raise
            mid = since + (until - since) / 2
            yield from _range(graph, account, since, mid, fields, level, extra)
            yield from _range(graph, account, mid, until, fields, level, extra)


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
