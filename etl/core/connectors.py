"""Where the ETL finds the orgs of a source and their credentials.

Decision 2026-10-17: until the backend's internal endpoints exist (ADR-0003, after Phase 4),
the ETL reads `public.organization_connectors` itself, as legacy did. Pipelines only see the
`ConnectorSource` interface, so moving to the API later changes this module alone.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from etl.core.model import Org
from etl.core.settings import Settings


class ConnectorSource(Protocol):
    def orgs(self, source: str) -> Sequence[Org]:
        """Every org with an enabled connector for `source`, sorted by org id."""
        ...


def shard(orgs: Sequence[Org], *, index: int, count: int) -> list[Org]:
    """The orgs of Cloud Run task `index` of `count`: sorted by id, then every count-th one.
    Each org lands in exactly one task, and the split is the same on every run."""
    if not 0 <= index < count:
        raise ValueError(f"Task index {index} is outside 0..{count - 1}.")
    ordered = sorted(orgs, key=lambda o: o.org_id)
    return [org for i, org in enumerate(ordered) if i % count == index]


def select_orgs(found: Sequence[Org], wanted: Sequence[str], settings: Settings) -> list[Org]:
    """The orgs a run works on: those named (default all), then RUN__ORG_ALLOWLIST (when set),
    then this task's shard."""
    orgs = [o for o in found if not wanted or o.org_id in wanted]
    allow = settings.run.org_allowlist
    if allow:
        orgs = [o for o in orgs if o.org_id in allow]
    return shard(orgs, index=settings.task_index, count=settings.task_count)
