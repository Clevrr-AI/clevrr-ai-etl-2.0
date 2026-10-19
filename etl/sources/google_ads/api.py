"""Google Ads REST client (legacy fetchers/google_ads_fetcher.py and utils/token_refresh.py).

- The OAuth token is refreshed from the connector's refresh_token at the start of every run and
  kept in memory. Legacy refreshed only near expiry and saved the new token back to
  organization_connectors; v2 never writes that table.
- `searchStream` is read with legacy's incremental parser (one response container at a time),
  retried 6 times with 1.6^n s + jitter on 429/5xx/network errors; a 403 with a
  login-customer-id is tried once without it; a 401, or a 403 that only re-auth fixes, fails
  the step.
- A manager account is expanded to its leaf accounts (customer_client), at most 25.
Tokens and response bodies are never logged.
"""

from __future__ import annotations

# Google Ads payloads are untyped JSON; read as legacy did.
# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false
import codecs
import contextlib
import json
import random
import re
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from etl.core.model import StepFailed

API_VERSION = "v24"  # legacy GOOGLE_ADS_API_VERSION default
BASE_URL = "https://googleads.googleapis.com"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - a URL, not a secret
RETRY_MAX = 6
RETRY_BASE = 1.6
MAX_CHILD_ACCOUNTS = 25
MAX_PENDING_JSON = 64 * 1024 * 1024
_USER_ACCESS_403_MARKERS = ("USER_PERMISSION_DENIED", "CUSTOMER_NOT_ENABLED", "NOT_ADS_USER")


class GoogleAdsError(StepFailed):
    def __init__(self, reason: str, status: int | None) -> None:
        super().__init__(reason, detail={"status": status})
        self.status = status


def customer_id(value: Any) -> str | None:
    """Digits only (legacy _normalize_customer_id)."""
    raw = str(value or "").strip()
    if not raw:
        return None
    return re.sub(r"\D", "", raw) or raw


def refresh_token(http: httpx.Client, credentials: dict[str, Any], client_id: str,
                  client_secret: str) -> str:  # fmt: skip
    token = str(credentials.get("refresh_token") or "")
    if not token:
        raise StepFailed("The Google Ads connector has no refresh_token.")
    form = {"client_id": client_id, "client_secret": client_secret,
            "grant_type": "refresh_token", "refresh_token": token}  # fmt: skip
    response = http.post(TOKEN_URL, data=form)
    if response.status_code != httpx.codes.OK.value:
        error = ""
        with contextlib.suppress(ValueError):
            error = str(response.json().get("error", ""))
        reason = (
            "reconnect Google Ads" if error == "invalid_grant" else f"HTTP {response.status_code}"
        )
        raise StepFailed(f"Google Ads token refresh failed: {reason}")
    access = str(response.json().get("access_token") or "")
    if not access:
        raise StepFailed("Google Ads token refresh returned no access_token.")
    return access


def parse_stream(chunks: Iterable[bytes]) -> Iterator[dict[str, Any]]:
    """Result rows of a searchStream body (a JSON array of containers), one container at a
    time (legacy _parse_search_stream_body: linear, bounded memory)."""
    utf8 = codecs.getincrementaldecoder("utf-8")()
    decoder = json.JSONDecoder()
    buf, started = "", False
    for raw in chunks:
        buf += utf8.decode(raw)
        while True:
            s = buf.lstrip()
            if not started:
                if not s:
                    buf = s
                    break
                if s[0] != "[":
                    raise GoogleAdsError("Unexpected searchStream body", None)
                s, started = s[1:].lstrip(), True
            if s[:1] == ",":
                s = s[1:].lstrip()
            if not s or s[0] == "]":
                buf = s
                break
            try:
                container, idx = decoder.raw_decode(s)
            except ValueError:
                buf = s
                if len(buf) > MAX_PENDING_JSON:
                    raise GoogleAdsError("searchStream buffer overflow", None) from None
                break
            buf = s[idx:]
            yield from (container.get("results") or []) if isinstance(container, dict) else []


@dataclass
class GoogleAds:
    http: httpx.Client
    access_token: str = field(repr=False)
    developer_token: str = field(repr=False)
    sleep: Callable[[float], None] = time.sleep

    def _headers(self, login: str | None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.access_token}",
                   "developer-token": self.developer_token,
                   "Content-Type": "application/json"}  # fmt: skip
        if login:
            headers["login-customer-id"] = login
        return headers

    def _once(self, cid: str, query: str, login: str | None) -> list[dict[str, Any]]:
        url = f"{BASE_URL}/{API_VERSION}/customers/{cid}/googleAds:searchStream?prettyPrint=false"
        with self.http.stream("POST", url, headers=self._headers(login),
                              json={"query": query}) as response:  # fmt: skip
            if response.status_code >= 400:
                body = response.read().decode("utf-8", "replace")
                raise _HttpFailure(response.status_code, body)
            return list(parse_stream(response.iter_bytes()))

    def search(self, cid: str, query: str, login: str | None = None) -> list[dict[str, Any]]:
        """All rows of a GAQL query; a retry re-reads the stream from the start (legacy)."""
        cid = customer_id(cid) or cid
        login = customer_id(login)
        last = ""
        for attempt in range(1, RETRY_MAX + 1):
            try:
                return self._once(cid, query, login)
            except _HttpFailure as exc:
                if exc.status == 429 or 500 <= exc.status < 600:
                    last = f"HTTP {exc.status}"
                else:
                    return self._fail_or_retry_without_login(exc, cid, query, login)
            except (httpx.TransportError, GoogleAdsError) as exc:
                last = type(exc).__name__
            self.sleep(RETRY_BASE**attempt + random.random())  # noqa: S311 - jitter
        raise GoogleAdsError(f"Google Ads searchStream failed after {RETRY_MAX} attempts: {last}",
                             None)  # fmt: skip

    def _fail_or_retry_without_login(self, exc: _HttpFailure, cid: str, query: str,
                                     login: str | None) -> list[dict[str, Any]]:  # fmt: skip
        _raise_if_auth(exc)
        if exc.status == 403 and login:
            try:
                return self._once(cid, query, None)
            except _HttpFailure as again:
                _raise_if_auth(again)
        kind = " (queryError)" if "queryError" in exc.body else ""
        raise GoogleAdsError(f"Google Ads searchStream HTTP {exc.status} for customer {cid}{kind}",
                             exc.status)  # fmt: skip

    def is_manager(self, cid: str, login: str | None) -> bool | None:
        query = (
            "SELECT customer.id, customer.manager, customer.descriptive_name FROM customer LIMIT 1"
        )
        try:
            rows = self.search(cid, query, login)
        except StepFailed:
            return None  # legacy: a failed probe means "not a manager"
        return rows[0].get("customer", {}).get("manager") if rows else None

    def leaf_accounts(self, manager: str, login: str | None) -> list[str]:
        query = "SELECT customer_client.id, customer_client.manager FROM customer_client"
        leaves: list[str] = []
        try:
            rows = self.search(manager, query, login or manager)
        except StepFailed:
            return []
        for row in rows:
            cc = row.get("customerClient", {})
            cid = customer_id(cc.get("id"))
            if cid and cid != manager and cc.get("manager") is False and cid not in leaves:
                leaves.append(cid)
            if len(leaves) >= MAX_CHILD_ACCOUNTS:
                break
        return leaves


class _HttpFailure(Exception):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status
        self.body = body


def _raise_if_auth(exc: _HttpFailure) -> None:
    if exc.status == 401:
        raise GoogleAdsError("Google Ads token rejected (HTTP 401): reconnect Google Ads", 401)
    if exc.status == 403 and any(m in exc.body for m in _USER_ACCESS_403_MARKERS):
        raise GoogleAdsError("Google Ads access denied (HTTP 403): reconnect Google Ads", 403)
