"""`ConnectorSource` over `public.organization_connectors` (decision 2026-10-17: until the
backend's internal API exists).

Legacy's nightly discovery, exactly (org_connector_service.fetch_enabled_connectors_by_type):
the connector name matches, the connector is enabled and the org is active. Legacy's single-org
path also ran disabled connectors (`reauth_required` ones included); this reader never does.
`encrypted_api_credentials` is plain JSON in legacy (it only `json.loads` it), not encrypted.
When an org has two enabled rows for one connector, the most recently updated one wins.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from psycopg import Connection
from psycopg.rows import TupleRow
from psycopg_pool import ConnectionPool

from etl.core.logging import get_logger
from etl.core.model import Org

log = get_logger(__name__)

_ENABLED = """
SELECT oc.organization_connector_id::text, oc.organization_id, oc.encrypted_api_credentials,
       oc.etl_config, COALESCE(oc.historic_done, false)
FROM public.organization_connectors oc
JOIN public.organizations o ON o.id = oc.organization_id
WHERE oc.connector_name = %(source)s AND oc.is_enabled AND o.is_active
ORDER BY oc.organization_id, oc.updated_at DESC NULLS LAST, oc.organization_connector_id
"""


class CredentialsError(Exception):
    """A connector's stored credentials are not a JSON object."""


def parse_credentials(raw: str | None, *, org_id: str, source: str) -> Mapping[str, Any]:
    if not raw:
        return {}
    try:
        value: object = json.loads(raw)
    except ValueError as exc:
        # The message names the org, never the value.
        raise CredentialsError(f"{source} credentials of {org_id} are not JSON") from exc
    if not isinstance(value, dict):
        raise CredentialsError(f"{source} credentials of {org_id} are not a JSON object")
    return dict(value)  # pyright: ignore[reportUnknownArgumentType]


class TableConnectorSource:
    def __init__(self, pool: ConnectionPool[Connection[TupleRow]]) -> None:
        self.pool = pool

    def orgs(self, source: str) -> Sequence[Org]:
        with self.pool.connection() as conn:
            rows = conn.execute(_ENABLED, {"source": source}).fetchall()
        found: dict[str, Org] = {}
        for connector_id, org_id, raw, config, historic_done in rows:
            if org_id in found:
                log.warning("etl.duplicate_connector", org_id=org_id, source=source,
                            kept=found[org_id].connector_id, skipped=connector_id)  # fmt: skip
                continue
            found[org_id] = Org(
                org_id=org_id, connector_id=connector_id, source=source,
                credentials=parse_credentials(raw, org_id=org_id, source=source),
                config=dict(config or {}), historic_done=bool(historic_done),
            )  # fmt: skip
        return list(found.values())
