"""The backend's internal API (ADR-0003): `POST /api/v2/internal/etl/landed` after a run, and
later the connector and credential endpoints.

Authentication: a Google-signed OIDC ID token for the job's service account, fetched from the
metadata server (Cloud Run), with the backend's audience. No shared secret. Off when
`API__BASE_URL` is unset (local runs, and today: the internal endpoints come after Phase 4).
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import httpx

from etl.core.logging import get_logger
from etl.core.settings import ApiSettings

log = get_logger(__name__)
METADATA_IDENTITY_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity"
)
RETRIES = 3
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


class ApiError(Exception):
    pass


class InternalApi:
    def __init__(self, settings: ApiSettings, http: httpx.Client) -> None:
        if not settings.base_url or not settings.audience:
            raise ValueError("API__BASE_URL and API__AUDIENCE are both needed.")
        self._base = settings.base_url.rstrip("/")
        self._audience = settings.audience
        self._timeout = settings.timeout_s
        self._http = http

    def _id_token(self) -> str:
        response = self._http.get(
            METADATA_IDENTITY_URL,
            params={"audience": self._audience, "format": "full"},
            headers={"Metadata-Flavor": "Google"},
            timeout=5.0,
        )
        response.raise_for_status()
        return response.text.strip()

    def post(self, path: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """POST with retries on transport errors and 429/5xx (1 s, 2 s backoff)."""
        last: Exception | None = None
        for attempt in range(RETRIES):
            try:
                response = self._http.post(
                    f"{self._base}{path}", json=dict(body), timeout=self._timeout,
                    headers={"Authorization": f"Bearer {self._id_token()}"},
                )  # fmt: skip
                if response.status_code not in RETRY_STATUS:
                    response.raise_for_status()
                    return dict(response.json())
                last = ApiError(f"{path} answered {response.status_code}")
            except httpx.TransportError as exc:
                last = exc
            time.sleep(2**attempt)
        raise ApiError(f"{path} failed after {RETRIES} tries") from last

    def landed(self, *, org_id: str, connector: str, tables: list[str], run_id: str,
               window: tuple[str, str]) -> None:  # fmt: skip
        """Tell the API that new data landed (onboarding `historic_done`, cache bump)."""
        self.post("/api/v2/internal/etl/landed", {"org_id": org_id, "connector": connector,
                  "tables": tables, "run_id": run_id, "window": list(window)})  # fmt: skip


def build_api(settings: ApiSettings, http: httpx.Client) -> InternalApi | None:
    if not settings.base_url:
        return None
    return InternalApi(settings, http)
