"""Shopify sales pipeline (plan 7.3): orders, order lines, refunds, refund lines, click
attribution, order-based ad-product mapping, and order_mv.

Steps, each idempotent:
1. `extract_sales`: orders into the raw tables. An incremental run asks for orders updated
   since the window start; that includes every order created in the window. A backfill asks
   for orders created in the window, in 30-day chunks.
2. `transform_sales`: the six `sql/sales_*.sql` files, in one transaction.
3. `sync_sales`: the run's orders to ClickHouse (ReplacingMergeTree tables: append), and the
   org's Shopify ad_product_mapping (replace).
4. `refresh_order_mv`: order_mv for the run's orders (`sql/ch_order_mv.sql`).
5. `check_sales`: raw vs Postgres vs ClickHouse for the run's orders.

"The run's orders" are the raw orders of the org in the run's window filter (`window_params`):
updated since the window start minus a day (incremental), or created in the window (backfill).
Legacy fetched one IST day plus an updated_at pass, transformed each file twice, synced every
org's rows and filtered them in pandas, and refreshed order_mv only for orders created that day.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, LiteralString
from zoneinfo import ZoneInfo

from psycopg import sql

from etl.core import ch
from etl.core.ch_writer import ChWriter, Slice
from etl.core.model import Mode, Org, Pipeline, RunContext, Step, StepResult, Window
from etl.core.pg_load import run_sql_file, statements
from etl.sources.shopify import sql_sales
from etl.sources.shopify.raw_sales import load_orders
from etl.sources.shopify.shop import connect

SQL_DIR = Path(__file__).resolve().parent / "sql"
TRANSFORMS = ("sales_orders.sql", "sales_order_lines.sql", "sales_refunds.sql",
              "sales_refund_lines.sql", "sales_click_attribution.sql",
              "sales_ad_product_mapping.sql")  # fmt: skip
IST = ZoneInfo("Asia/Kolkata")
POS_EXCLUDED_ORGS = frozenset({"mydesignation.com"})  # legacy etl_runner.POS_EXCLUDED_ORG_IDS
BACKFILL_CHUNK = timedelta(days=30)
ID_CHUNK = 1000
_ORDERS_PARAMS = {"limit": 250, "status": "any"}  # legacy's connector_endpoints row


def exclude_pos(org: Org) -> bool:
    """Legacy: always for the listed orgs, else `etl_config.exclude_pos`. Legacy's updated_at
    pass dropped the org rule; v2 applies it to every order."""
    flag = org.config.get("exclude_pos")
    return org.org_id.lower() in POS_EXCLUDED_ORGS or str(flag).lower() in {"true", "1", "yes"}


def _ist_naive(moment: datetime) -> datetime:
    return moment.astimezone(IST).replace(tzinfo=None)


def window_params(ctx: RunContext, org: Org, window: Window) -> dict[str, Any]:
    """The run's orders, in the raw tables' terms (shop wall-clock time, IST for the pilots).
    The one-day margin covers a shop in another zone; a re-transform is idempotent."""
    if ctx.mode == Mode.BACKFILL:
        since, start, end = datetime.max, _ist_naive(window.start), _ist_naive(window.end)
    else:
        since = _ist_naive(window.start - timedelta(days=1))
        start = end = since
    return {"org": org.org_id, "exclude_pos": exclude_pos(org), "since": since,
            "created_from": start, "created_to": end}  # fmt: skip


def fetch_passes(ctx: RunContext, window: Window) -> Iterator[dict[str, Any]]:
    if ctx.mode != Mode.BACKFILL:
        yield {**_ORDERS_PARAMS, "updated_at_min": window.start.isoformat()}
        return
    start = window.start
    while start < window.end:
        end = min(start + BACKFILL_CHUNK, window.end)
        yield {**_ORDERS_PARAMS, "created_at_min": start.isoformat(),
               "created_at_max": (end - timedelta(seconds=1)).isoformat()}  # fmt: skip
        start = end


def extract_sales(ctx: RunContext, org: Org, window: Window) -> StepResult:
    with connect(ctx, org) as client, ctx.pg.connection() as conn:
        counts = load_orders(conn, org.org_id, client, fetch_passes(ctx, window))
    return StepResult(counts["raw_shopify_orders"], sum(counts.values()) - counts["pages"],
                      detail=counts)  # fmt: skip


def transform_sales(ctx: RunContext, org: Org, window: Window) -> StepResult:
    params = window_params(ctx, org, window)
    detail: dict[str, list[int]] = {}
    with ctx.pg.connection() as conn, conn.transaction():
        for name in TRANSFORMS:
            detail[name.removesuffix(".sql")] = run_sql_file(conn, SQL_DIR / name, params)
    return StepResult(detail=detail)


def _rows(ctx: RunContext, query: LiteralString | sql.Composed,
          params: Mapping[str, Any]) -> list[tuple[Any, ...]]:  # fmt: skip
    with ctx.pg.connection() as conn:
        return conn.execute(query, dict(params)).fetchall()


def run_order_ids(ctx: RunContext, params: Mapping[str, Any]) -> list[str]:
    return [str(r[0]) for r in _rows(ctx, sql_sales.RUN_ORDERS, params)]


def _chunks(items: Sequence[str], size: int = ID_CHUNK) -> Iterator[list[str]]:
    for i in range(0, len(items), size):
        yield list(items[i : i + size])


def sync_sales(ctx: RunContext, org: Org, window: Window) -> StepResult:
    params = window_params(ctx, org, window)
    writer = ChWriter(ctx.ch, ctx.settings.clickhouse)
    detail: dict[str, int] = {}
    for table in sql_sales.SYNCED:
        columns = writer.columns(table)
        rows = [list(r) for r in _rows(ctx, sql_sales.run_select(table, columns), params)]
        detail[table] = writer.append(table, columns, rows)
    columns = writer.columns("ad_product_mapping")
    mapping = [list(r) for r in _rows(ctx, sql_sales.shopify_mapping_select(columns), params)]
    shopify_only = Slice(source_column="source_type", source="shopify")
    detail["ad_product_mapping"] = writer.replace(
        "ad_product_mapping", org.org_id, shopify_only, columns, mapping
    )
    total = sum(detail.values())
    return StepResult(total, total, detail=detail)


def refresh_order_mv(ctx: RunContext, org: Org, window: Window) -> StepResult:
    ids = run_order_ids(ctx, window_params(ctx, org, window))
    (sql,) = statements((SQL_DIR / "ch_order_mv.sql").read_text(encoding="utf-8"))
    settings = {"insert_deduplicate": 0}
    if ctx.settings.clickhouse.cluster:
        settings |= {"insert_distributed_sync": 1, "insert_quorum": "auto"}
    for chunk in _chunks(ids):
        ch.command(ctx.ch, sql, {"org": org.org_id, "ids": chunk}, settings=settings)
    return StepResult(len(ids), len(ids))


def _ch_totals(ctx: RunContext, query: str, org_id: str, ids: Sequence[str],
               width: int) -> list[Any]:  # fmt: skip
    totals: list[Any] = [0] * width
    for chunk in _chunks(ids):
        totals = sql_sales.add(totals, ch.rows(ctx.ch, query, {"org": org_id, "ids": chunk})[0])
    return totals


def check_sales(ctx: RunContext, org: Org, window: Window) -> StepResult:
    """Every raw order of the run has one Postgres order; Postgres and ClickHouse (FINAL) agree
    on rows and money for the run's orders; order_mv has a row per order."""
    params = window_params(ctx, org, window)
    ids = run_order_ids(ctx, params)
    detail: dict[str, Any] = {"run_orders": len(ids)}
    for table, pg_exprs, ch_exprs, labels in sql_sales.FIGURES:
        pg = _rows(ctx, sql_sales.pg_figures(table, pg_exprs), params)[0]
        if table == "orders":
            sql_sales.compare("orders (raw vs Postgres)", ("rows",), [len(ids)], pg[:1])
        ch_values = _ch_totals(ctx, sql_sales.ch_figures(table, ch_exprs), org.org_id, ids,
                               len(labels))  # fmt: skip
        detail[table] = sql_sales.compare(table, labels, pg, ch_values)
    mv = _ch_totals(ctx, sql_sales.ORDER_MV_ROWS, org.org_id, ids, 1)
    detail["order_mv"] = sql_sales.compare("order_mv", ("rows",), [len(ids)], mv)
    return StepResult(detail=detail)


SHOPIFY_SALES = Pipeline(
    name="shopify-sales",
    source="shopify",
    steps=(
        Step("extract_sales", extract_sales),
        Step("transform_sales", transform_sales),
        Step("sync_sales", sync_sales),
        Step("refresh_order_mv", refresh_order_mv),
        Step("check_sales", check_sales),
    ),
    lookback=timedelta(hours=6),  # re-ask for late edits; the transform adds a day
    first_window=timedelta(days=3),  # a new org's history comes from a backfill run
    timeout=timedelta(hours=2),
    schedule="0 3 * * *",  # legacy etl-all-shopify: 08:30 IST
)
