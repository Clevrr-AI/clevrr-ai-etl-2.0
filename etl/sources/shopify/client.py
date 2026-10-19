"""Shopify Admin REST client: one shop, read-only GETs.

The requests are legacy's (`fetchers/shopify_fetcher.py`): `{url}/admin/api/{version}/{endpoint}
.json`, the first page with the endpoint's params, then `Link: <...>; rel="next"` until the end.
429, 5xx and network errors are retried 7 times (1, 2, 4 ... 32 s, or Retry-After up to 60 s).

Differences from legacy:
- Reaching the page cap fails the step. Legacy logged a warning and kept a partial result.
- A 401 or 403 fails the step and names the cause. Legacy parked the connector by writing the
  shared connector table; v2 never writes it.

A shop with no `access_token` gets one from the client-credentials grant, kept in memory only,
as legacy did (`utils/token_refresh.py`). Tokens and response bodies are never logged.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from etl.core.model import StepFailed

RETRYABLE = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 7
BACKOFF_CAP_S = 32.0
RETRY_AFTER_CAP_S = 60.0
MAX_PAGES = 1000


@dataclass(frozen=True, slots=True)
class Shop:
    url: str  # https://<shop>.myshopify.com
    api_version: str
    token: str = field(repr=False)


def shop_url(credentials: Mapping[str, Any]) -> str:
    url = str(credentials.get("url") or "").strip().rstrip("/")
    if not url.startswith("https://"):
        raise StepFailed("The Shopify connector has no https shop url.")
    return url


def access_token(credentials: Mapping[str, Any], http: httpx.Client) -> str:
    """The stored token, else a new one from the client-credentials grant (not stored)."""
    token = str(credentials.get("access_token") or "").strip()
    if token:
        return token
    client_id = str(credentials.get("client_id") or "").strip()
    client_secret = str(credentials.get("client_secret") or "").strip()
    if not client_id or not client_secret:
        raise StepFailed("The Shopify connector has no access_token and no client credentials.")
    body = {"client_id": client_id, "client_secret": client_secret,
            "grant_type": "client_credentials"}  # fmt: skip
    try:
        response = http.post(shop_url(credentials) + "/admin/oauth/access_token", json=body)
    except httpx.HTTPError as exc:
        raise StepFailed(f"Shopify token request failed: {type(exc).__name__}") from None
    if response.status_code != 200:
        raise StepFailed(f"Shopify token request failed: HTTP {response.status_code}")
    token = str(response.json().get("access_token") or "")
    if not token:
        raise StepFailed("Shopify token response has no access_token.")
    return token


def next_link(header: str | None) -> str | None:
    for part in (header or "").split(","):
        if 'rel="next"' in part:
            return part.split(";")[0].strip().strip("<>")
    return None


class ShopifyClient:
    def __init__(self, shop: Shop, http: httpx.Client, *, max_rpm: int = 40,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:  # fmt: skip
        self.shop = shop
        self.http = http
        self.interval = 60.0 / max_rpm
        self.sleep = sleep
        self.clock = clock
        self._last = float("-inf")

    def pages(self, endpoint: str, params: Mapping[str, Any],
              list_key: str) -> Iterator[list[dict[str, Any]]]:  # fmt: skip
        """Every page of `endpoint`, in order. Raises StepFailed past MAX_PAGES."""
        url: str | None = f"{self.shop.url}/admin/api/{self.shop.api_version}/{endpoint}.json"
        query: Mapping[str, Any] | None = params
        count = 0
        while url:
            count += 1
            if count > MAX_PAGES:
                raise StepFailed(f"Shopify {endpoint}: more than {MAX_PAGES} pages",
                                 detail={"endpoint": endpoint, "pages": MAX_PAGES})  # fmt: skip
            response = self._get(url, query, endpoint)
            items = response.json().get(list_key, [])
            yield list(items)
            url, query = next_link(response.headers.get("Link")), None

    def _get(self, url: str, params: Mapping[str, Any] | None, endpoint: str) -> httpx.Response:
        headers = {"X-Shopify-Access-Token": self.shop.token}
        reason = ""
        for attempt in range(MAX_ATTEMPTS):
            self._throttle()
            retry_after: str | None = None
            try:
                response = self.http.get(
                    url, params=dict(params) if params else None, headers=headers
                )
            except httpx.TransportError as exc:
                reason = type(exc).__name__
            else:
                status = response.status_code
                if status in (401, 403):
                    raise StepFailed(f"Shopify {endpoint}: HTTP {status} (token or scope)",
                                     detail={"status": status})  # fmt: skip
                if status not in RETRYABLE:
                    response.raise_for_status()
                    return response
                reason = f"HTTP {status}"
                retry_after = response.headers.get("Retry-After")
            if attempt + 1 < MAX_ATTEMPTS:
                self.sleep(_backoff(attempt, retry_after))
        raise StepFailed(f"Shopify {endpoint}: {reason} after {MAX_ATTEMPTS} attempts",
                         detail={"endpoint": endpoint, "reason": reason})  # fmt: skip

    def _throttle(self) -> None:
        wait = self._last + self.interval - self.clock()
        if wait > 0:
            self.sleep(wait)
        self._last = self.clock()


def _backoff(attempt: int, retry_after: str | None) -> float:
    try:
        seconds = float(retry_after or 0)
    except ValueError:
        seconds = 0
    if seconds > 0:
        return min(seconds, RETRY_AFTER_CAP_S)
    return min(2.0**attempt, BACKOFF_CAP_S)
