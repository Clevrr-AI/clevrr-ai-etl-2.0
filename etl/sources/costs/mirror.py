"""Costs mirror (plan 7.3): Postgres `product_cost_history` and `variable_cost_rules_versions`
to ClickHouse, one org at a time.

The app writes manual costs and the variable cost rules in `public`, and the catalog seed adds
`shopify_price` rows there, so the mirror reads `public`. After the cutover the target schema is
`public` too. Legacy appended each org's full history every night to non-Replacing tables, with
no delete. On 2026-10-17 sandbox held about 66 copies of each mirrored row, and one pilot org was
never mirrored at all. v2 replaces the org's rows (delete, check empty, insert). The check step
then compares rows, distinct ids and totals with Postgres.

Values keep the shapes of legacy's Parquet load: bigint ids become strings, jsonb objects become
`json.dumps` text, and a null `scope_key` becomes '' because the column is not Nullable.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import timedelta
from typing import Any, Final, LiteralString

from etl.checks.compare import same_figures
from etl.core import ch
from etl.core.ch_writer import ChWriter, Slice
from etl.core.model import Org, Pipeline, RunContext, Step, StepResult, Window

PCH_COLUMNS = (
    "id", "organization_id", "product_id", "variant_id", "sku", "cost_per_item", "currency",
    "source", "effective_at", "expires_at", "updated_at", "updated_by", "meta_json", "created_at",
)  # fmt: skip
VCRV_COLUMNS = (
    "id", "organization_id", "component", "unit", "value", "scope_type", "scope_key",
    "currency", "updated_at", "updated_by", "date_start", "created_at", "recurrence",
    "date_stop", "meta_json",
)  # fmt: skip
_PCH_SELECT: Final = (
    "SELECT id, organization_id, product_id, variant_id, sku, cost_per_item, currency, source,"
    " effective_at, expires_at, updated_at, updated_by, meta_json, created_at"
    " FROM public.product_cost_history WHERE organization_id = %(org)s ORDER BY id"
)
_VCRV_SELECT: Final = (
    "SELECT id, organization_id, component, unit, value, scope_type, scope_key, currency,"
    " updated_at, updated_by, date_start, created_at, recurrence, date_stop, meta_json"
    " FROM public.variable_cost_rules_versions WHERE organization_id = %(org)s ORDER BY id"
)
_PCH_FIGURES_PG: Final = (
    "SELECT count(*), count(DISTINCT id), sum(cost_per_item),"
    " count(*) FILTER (WHERE expires_at IS NULL)"
    " FROM public.product_cost_history WHERE organization_id = %(org)s"
)
_PCH_FIGURES_CH: Final = (
    "SELECT count(), uniqExact(id), sum(cost_per_item), countIf(expires_at IS NULL)"
    " FROM product_cost_history WHERE organization_id = {org:String}"
)
_VCRV_FIGURES_PG: Final = (
    "SELECT count(*), count(DISTINCT id), sum(value)"
    " FROM public.variable_cost_rules_versions WHERE organization_id = %(org)s"
)
_VCRV_FIGURES_CH: Final = (
    "SELECT count(), uniqExact(id), sum(value)"
    " FROM variable_cost_rules_versions WHERE organization_id = {org:String}"
)


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def _json_text(value: Any) -> str | None:
    """Legacy ran `json.dumps` on dict values only and passed strings through."""
    if isinstance(value, dict | list):
        return json.dumps(value)
    return _text(value)


def pch_row(row: Sequence[Any]) -> list[Any]:
    out = list(row)
    out[2] = str(row[2])  # product_id: bigint -> LowCardinality(String)
    out[3] = _text(row[3])  # variant_id: bigint -> Nullable(String)
    out[12] = _json_text(row[12])  # meta_json: jsonb -> Nullable(String)
    return out


def vcrv_row(row: Sequence[Any]) -> list[Any]:
    out = list(row)
    out[6] = "" if row[6] is None else row[6]  # scope_key: the column is not Nullable
    out[14] = _json_text(row[14])
    return out


def _read(ctx: RunContext, sql: LiteralString, org_id: str) -> list[tuple[Any, ...]]:
    with ctx.pg.connection() as conn:
        return conn.execute(sql, {"org": org_id}).fetchall()


def mirror_product_costs(ctx: RunContext, org: Org, window: Window) -> StepResult:
    rows = [pch_row(r) for r in _read(ctx, _PCH_SELECT, org.org_id)]
    writer = ChWriter(ctx.ch, ctx.settings.clickhouse)
    written = writer.replace("product_cost_history", org.org_id, Slice(), PCH_COLUMNS, rows)
    return StepResult(len(rows), written)


def mirror_variable_costs(ctx: RunContext, org: Org, window: Window) -> StepResult:
    rows = [vcrv_row(r) for r in _read(ctx, _VCRV_SELECT, org.org_id)]
    writer = ChWriter(ctx.ch, ctx.settings.clickhouse)
    written = writer.replace("variable_cost_rules_versions", org.org_id, Slice(), VCRV_COLUMNS,
                             rows)  # fmt: skip
    return StepResult(len(rows), written)


def check_costs(ctx: RunContext, org: Org, window: Window) -> StepResult:
    params = {"org": org.org_id}
    pch = same_figures(
        "product_cost_history", ("rows", "ids", "cost_total", "open_rows"),
        _first(_read(ctx, _PCH_FIGURES_PG, org.org_id)),
        _first(ch.rows(ctx.ch, _PCH_FIGURES_CH, params)),
    )  # fmt: skip
    vcrv = same_figures(
        "variable_cost_rules_versions", ("rows", "ids", "value_total"),
        _first(_read(ctx, _VCRV_FIGURES_PG, org.org_id)),
        _first(ch.rows(ctx.ch, _VCRV_FIGURES_CH, params)),
    )  # fmt: skip
    detail = {"product_cost_history": {k: str(v) for k, v in pch.items()},
              "variable_cost_rules_versions": {k: str(v) for k, v in vcrv.items()}}  # fmt: skip
    return StepResult(detail=detail)


def _first(rows: Sequence[Sequence[Any]]) -> Sequence[Any] | None:
    return rows[0] if rows else None


COSTS = Pipeline(
    name="costs",
    source="shopify",  # costs belong to the Shopify catalog: orgs with an enabled Shopify connector
    steps=(
        Step("mirror_product_costs", mirror_product_costs),
        Step("mirror_variable_costs", mirror_variable_costs),
        Step("check_costs", check_costs),
    ),
    lookback=timedelta(0),  # a full mirror each run: the window is not used
    first_window=timedelta(days=1),
    timeout=timedelta(minutes=30),
    schedule="30 21 * * *",  # legacy etl-products-cost: 03:00 IST
)
