"""SQL for the Shopify sales pipeline's sync and check steps (a builder file: identifiers
come from fixed constants and from ClickHouse's own column list, values are bound).

"The run's orders" (`RUN_ORDERS`) is one filter for every step after the transform, so the
sync, order_mv and the check all cover exactly the orders the transform wrote.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final, LiteralString

from psycopg import sql

from etl.checks.compare import close_figures

RUN_ORDERS: Final = (
    "SELECT ro.id FROM raw_shopify_orders ro WHERE ro.organization_id = %(org)s"
    " AND (NOT %(exclude_pos)s OR upper(coalesce(ro.source_name, '')) <> 'POS')"
    " AND (ro.updated_at >= %(since)s"
    " OR (ro.created_at >= %(created_from)s AND ro.created_at < %(created_to)s))"
)
# ReplacingMergeTree tables in ClickHouse: the run's rows are appended, newer versions win.
SYNCED = ("orders", "order_lines", "refunds", "refund_lines", "order_click_attribution")
# (table, Postgres figures, ClickHouse figures, labels); "rows" compares exactly, the rest are
# money (Postgres numeric vs ClickHouse Float64) and compare to the cent.
FIGURES: tuple[tuple[str, LiteralString, LiteralString, tuple[str, ...]], ...] = (
    ("orders", "count(*), sum(total_price)", "count(), sum(total_price)", ("rows", "total_price")),
    ("order_lines", "count(*), sum(quantity * item_price)", "count(), sum(quantity * item_price)",
     ("rows", "line_value")),
    ("refunds", "count(*), sum(refund_amount)", "count(), sum(refund_amount)",
     ("rows", "refund_amount")),
    ("refund_lines", "count(*), sum(subtotal_amount)", "count(), sum(subtotal_amount)",
     ("rows", "subtotal_amount")),
    ("order_click_attribution", "count(*)", "count()", ("rows",)),
)  # fmt: skip


def run_select(table: str, columns: Sequence[str]) -> sql.Composed:
    """The run's rows of `table`, in `columns` (ClickHouse's column list)."""
    return sql.SQL("SELECT {cols} FROM {t} WHERE organization_id = %(org)s AND order_id IN ("
                   + RUN_ORDERS + ")").format(
        cols=sql.SQL(", ").join(sql.Identifier(c) for c in columns), t=sql.Identifier(table)
    )  # fmt: skip


def shopify_mapping_select(columns: Sequence[str]) -> sql.Composed:
    return sql.SQL(
        "SELECT {cols} FROM ad_product_mapping"
        " WHERE organization_id = %(org)s AND source_type = 'shopify'"
    ).format(cols=sql.SQL(", ").join(sql.Identifier(c) for c in columns))


def pg_figures(table: str, exprs: LiteralString) -> sql.Composed:
    query = ("SELECT " + exprs + " FROM {t} WHERE organization_id = %(org)s"
             " AND order_id IN (" + RUN_ORDERS + ")")  # fmt: skip
    return sql.SQL(query).format(t=sql.Identifier(table))


def ch_figures(table: str, exprs: LiteralString) -> str:
    return ("SELECT " + exprs + " FROM " + table + " FINAL WHERE organization_id = {org:String}"
            " AND toString(order_id) IN {ids:Array(String)}")  # fmt: skip


ORDER_MV_ROWS: Final = ch_figures("order_mv", "count()")


def add(totals: list[Any], row: Sequence[Any]) -> list[Any]:
    """Sum figures over id chunks (None counts as 0)."""
    return [(t or 0) + (v or 0) for t, v in zip(totals, row, strict=True)]


compare = close_figures  # rows exactly, money to the cent
