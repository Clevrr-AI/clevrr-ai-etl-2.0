"""Compare one org's figures in Postgres and ClickHouse after a load (plan 7.4).

A difference fails the step: the org's run fails and the alert names the table and both sets of
figures. Figures are counts and totals, never row values.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from etl.core.model import StepFailed


def _norm(value: Any) -> Any:
    if value is None:  # sum() over no rows: NULL in Postgres, 0 in ClickHouse
        return 0
    if isinstance(value, Decimal):
        return value.normalize() if value else Decimal(0)
    return value


def same_figures(table: str, labels: Sequence[str], pg: Sequence[Any] | None,
                 ch: Sequence[Any] | None) -> dict[str, Any]:  # fmt: skip
    """The figures, by label, when both sides agree. Raises StepFailed when they differ."""
    pg_values = [_norm(v) for v in (pg or [])]
    ch_values = [_norm(v) for v in (ch or [])]
    if len(pg_values) != len(labels) or pg_values != ch_values:
        detail = {"table": table, "postgres": dict(zip(labels, map(str, pg_values), strict=False)),
                  "clickhouse": dict(zip(labels, map(str, ch_values), strict=False))}  # fmt: skip
        raise StepFailed(f"{table}: ClickHouse differs from Postgres", detail=detail)
    return dict(zip(labels, pg_values, strict=True))


CENT = 0.011


def close_figures(table: str, labels: Sequence[str], pg: Sequence[Any] | None,
                  ch: Sequence[Any] | None) -> dict[str, str]:  # fmt: skip
    """Like `same_figures`, for money kept in different types (Postgres numeric, ClickHouse
    Float64): a figure labelled "rows" compares exactly, the others to the cent."""
    pg_values, ch_values = list(pg or []), list(ch or [])
    if len(pg_values) != len(labels) or len(ch_values) != len(labels):
        raise StepFailed(f"{table}: no figures to compare", detail={"table": table})
    for label, a, b in zip(labels, pg_values, ch_values, strict=True):
        same = int(a or 0) == int(b or 0) if label == "rows" else (
            abs(float(a or 0) - float(b or 0)) <= CENT)  # fmt: skip
        if not same:
            detail = {"table": table, "figure": label, "postgres": str(a), "clickhouse": str(b)}
            raise StepFailed(f"{table}: {label} differs (Postgres {a}, ClickHouse {b})",
                             detail=detail)  # fmt: skip
    return {label: str(v) for label, v in zip(labels, pg_values, strict=True)}
