"""Demo data for the demo org, run by hand (legacy ETL/demo_data/generator.py):

    uv run python -m tools.demo.run --mode backfill            # 2025-12-01 to yesterday
    uv run python -m tools.demo.run --mode range --start 2026-01-01 --end 2026-01-31
    uv run python -m tools.demo.run --mode daily               # yesterday
    add --dry-run to only count rows

The generators (config.py, *_generator.py) are legacy's files, copied unchanged. This loader
writes their rows to the configured target schema and ClickHouse database through the ETL
core. Changed from legacy:
- ClickHouse rows are replaced (the org's catalog; the run's days for the daily tables), not
  appended: legacy re-appended the whole history every day.
- The generators are seeded (--seed, default 42): legacy's unseeded runs moved order ids to
  new dates on every run.
- product_cost_history gets ids from its sequence after the org's demo rows are deleted;
  legacy's ids 1..N collided with other orgs' rows and were skipped (ON CONFLICT DO NOTHING).
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from psycopg import sql

from etl.core import ch
from etl.core.ch_writer import ChWriter, Slice
from etl.core.db import open_clickhouse, open_pg_pool
from etl.core.logging import configure_logging, get_logger
from etl.core.pg_load import upsert
from etl.core.settings import load_settings
from tools.demo.config import BACKFILL_START_DATE, DEMO_ORG_ID
from tools.demo.delhivery_generator import DelhiveryDataGenerator
from tools.demo.meta_ads_generator import MetaAdsDataGenerator
from tools.demo.shopify_generator import ShopifyDataGenerator

log = get_logger("tools.demo")
# Postgres upsert keys (legacy's ON CONFLICT targets).
PG_KEYS = {
    "products": ("product_id", "organization_id"),
    "product_variants": ("variant_id", "organization_id"),
    "orders": ("order_id", "organization_id"),
    "order_lines": ("order_item_id", "organization_id"),
    "refunds": ("refund_id", "organization_id"),
    "refund_lines": ("id", "organization_id"),
    "shipments": ("organization_id", "waybill", "source_type"),
    "shipment_scans": ("organization_id", "waybill", "source_type", "scan_datetime", "status_code"),
}
# The time column a daily-table replace deletes by in ClickHouse.
CH_DAY_COLUMN = {"orders": "created_at", "order_lines": "created_at", "refunds": "created_at",
                 "refund_lines": "created_at", "shipments": "created_at",
                 "shipment_scans": "scan_datetime", "advertising": "created_at"}  # fmt: skip
_PG_COLUMNS = (
    "SELECT column_name FROM information_schema.columns"
    " WHERE table_schema = current_schema() AND table_name = %(t)s"
)


def _refund_defaults(rows: list[dict[str, Any]]) -> None:
    """Legacy filled these before its ClickHouse insert."""
    for r in rows:
        r.setdefault("updated_at", r.get("created_at"))
        r.setdefault("kind", "refund")


class Loader:
    def __init__(self, org_id: str, dry_run: bool) -> None:
        settings = load_settings()
        self.org_id, self.dry_run = org_id, dry_run
        self.pool = open_pg_pool(settings.postgres)
        self.ch = open_clickhouse(settings.clickhouse)
        self.writer = ChWriter(self.ch, settings.clickhouse)
        self.cluster = settings.clickhouse.cluster

    def close(self) -> None:
        self.pool.close()
        self.ch.close()

    def postgres(self, table: str, rows: Sequence[dict[str, Any]]) -> int:
        if self.dry_run or not rows:
            return len(rows)
        with self.pool.connection() as conn:
            have = {r[0] for r in conn.execute(_PG_COLUMNS, {"t": table}).fetchall()}
            columns = [c for c in rows[0] if c in have]
            data = [[r.get(c) for c in columns] for r in rows]
            with conn.transaction():
                return upsert(conn, table, columns, PG_KEYS[table], data)

    def clickhouse(self, table: str, rows: Sequence[dict[str, Any]], slice_: Slice) -> int:
        if self.dry_run:
            return len(rows)
        columns = [c for c in self.writer.columns(table) if any(c in r for r in rows[:1])]
        data = [[r.get(c) for c in columns] for r in rows]
        return self.writer.replace(table, self.org_id, slice_, columns, data)

    def replace_waybills(self, rows: Sequence[dict[str, Any]]) -> None:
        """Shipments carry the generation time as created_at, so no date span finds an earlier
        run's rows: delete the run's waybills, then insert."""
        if self.dry_run:
            return
        waybills = sorted({str(r["waybill"]) for r in rows})
        target = (
            "shipments_shard ON CLUSTER '" + self.cluster + "'" if self.cluster else "shipments"
        )
        delete = ("ALTER TABLE " + target + " DELETE WHERE organization_id = {o:String}"
                  " AND waybill IN {w:Array(String)} SETTINGS mutations_sync = 2")  # fmt: skip
        for i in range(0, len(waybills), 1000):
            ch.command(self.ch, delete, {"o": self.org_id, "w": waybills[i : i + 1000]})
        columns = [c for c in self.writer.columns("shipments") if c in rows[0]]
        self.writer.append("shipments", columns, [[r.get(c) for c in columns] for r in rows])

    def cost_history(self, rows: list[dict[str, Any]]) -> None:
        """Legacy seeded one 'shopify_demo' row per variant."""
        if self.dry_run:
            return
        with self.pool.connection() as conn, conn.transaction():
            conn.execute("DELETE FROM product_cost_history WHERE organization_id = %(o)s"
                         " AND source = 'shopify_demo'", {"o": self.org_id})  # fmt: skip
            columns = [c for c in rows[0] if c != "id"] if rows else []
            with conn.cursor() as cur:
                for r in rows:
                    cur.execute(_insert_sql(columns), [r[c] for c in columns])
        self.clickhouse("product_cost_history", rows, Slice())


def _insert_sql(columns: Sequence[str]) -> sql.Composed:
    return sql.SQL("INSERT INTO product_cost_history ({c}) VALUES ({v})").format(
        c=sql.SQL(", ").join(map(sql.Identifier, columns)),
        v=sql.SQL(", ").join(sql.Placeholder() * len(columns)),
    )


def cost_rows(org_id: str, products: list[dict[str, Any]],
              variants: list[dict[str, Any]]) -> list[dict[str, Any]]:  # fmt: skip
    """Legacy _build_cost_history_entries, unchanged."""
    by_product = {p["product_id"]: p for p in products}
    rows = []
    for counter, v in enumerate(variants, 1):
        product = by_product.get(v["product_id"], {})
        cost = v.get("cost_per_item") or product.get("cost_per_item") or 0
        effective = product.get("created_at") or BACKFILL_START_DATE
        rows.append({"id": counter, "organization_id": org_id, "product_id": v["product_id"],
                     "variant_id": v["variant_id"], "sku": v.get("sku"), "cost_per_item": cost,
                     "currency": "INR", "source": "shopify_demo", "effective_at": effective,
                     "expires_at": None, "updated_at": effective, "updated_by": "demo_generator",
                     "meta_json": None, "created_at": effective})  # fmt: skip
    return rows


def run(loader: Loader, start: datetime, end: datetime, seed: int, catalog: bool) -> dict[str, int]:
    shopify = ShopifyDataGenerator(org_id=loader.org_id, seed=seed)
    meta = MetaAdsDataGenerator(org_id=loader.org_id, seed=seed)
    delhivery = DelhiveryDataGenerator(org_id=loader.org_id, seed=seed)
    counts: dict[str, int] = {}
    if catalog:
        products, variants = shopify.get_products(), shopify.get_variants()
        counts["products"] = loader.postgres("products", products)
        counts["product_variants"] = loader.postgres("product_variants", variants)
        loader.clickhouse("products", products, Slice())
        loader.clickhouse("product_variants", variants, Slice())
        loader.cost_history(cost_rows(loader.org_id, products, variants))
    daily: dict[str, list[dict[str, Any]]] = {t: [] for t in CH_DAY_COLUMN}
    day = start
    while day <= end:
        orders, lines, refunds, refund_lines = shopify.generate_orders_for_date(day)
        shipments, scans = delhivery.generate_shipments_and_scans_for_orders(orders, end)
        for table, rows in (("orders", orders), ("order_lines", lines), ("refunds", refunds),
                            ("refund_lines", refund_lines), ("shipments", shipments),
                            ("shipment_scans", scans),
                            ("advertising", meta.generate_ads_for_date(day))):  # fmt: skip
            daily[table].extend(rows)
        day += timedelta(days=1)
    _refund_defaults(daily["refunds"])
    for table, rows in daily.items():
        if table != "advertising":  # legacy: advertising went to ClickHouse only
            counts[table] = loader.postgres(table, rows)
        if rows and table == "shipments":
            loader.replace_waybills(rows)
        elif rows:
            # A refund is dated after its order, outside the run's days: delete the span the
            # rows themselves cover, so a rerun replaces them.
            column = CH_DAY_COLUMN[table]
            stamps = [r[column] for r in rows if r.get(column) is not None]
            span = Slice(time_column=column, start=min(stamps),
                         end=max(stamps) + timedelta(seconds=1))  # fmt: skip
            loader.clickhouse(table, rows, span)
        counts.setdefault(table, len(rows))
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Demo data for the demo org (manual).")
    parser.add_argument("--mode", choices=["backfill", "daily", "range"], default="daily")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--org-id", default=DEMO_ORG_ID)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    configure_logging()
    yesterday = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(1)
    if args.mode == "range":
        if not (args.start and args.end):
            parser.error("--start and --end are required with --mode range")
        start, end = datetime.fromisoformat(args.start), datetime.fromisoformat(args.end)
    elif args.mode == "backfill":
        start, end = BACKFILL_START_DATE, yesterday
    else:
        start = end = yesterday
    loader = Loader(args.org_id, args.dry_run)
    try:
        counts = run(loader, start, end, args.seed, catalog=args.mode == "backfill")
    finally:
        loader.close()
    log.info("demo.done", org_id=args.org_id, mode=args.mode, dry_run=args.dry_run, **counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
