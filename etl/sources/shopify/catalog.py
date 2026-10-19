"""Shopify catalog pipeline (plan 7.3): products, variants, collections, and the cost seed.

Steps, each idempotent:
1. `extract_catalog`: full pulls of products, collections and collects into the raw tables, in
   one transaction (`raw_catalog.load_catalog`).
2. `transform_catalog`: products, variants, prune, cost seed (`sql/`), in one transaction.
3. `sync_catalog`: replace the org's `products` and `product_variants` in ClickHouse.
4. `check_catalog`: raw vs Postgres vs ClickHouse figures.

Legacy (CronJob `etl-all-shopify-catalog-daily`, 18:30 UTC) wiped the org's catalog first and
committed the wipe before the fetch, so a failed fetch left the org with no catalog. It also
erased the app's manual costs, fetched only the collects updated the day before, and stopped
silently at 1000 pages.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any, Final, LiteralString

from etl.checks.compare import same_figures
from etl.core import ch
from etl.core.ch_writer import ChWriter, Slice
from etl.core.model import Org, Pipeline, RunContext, Step, StepResult, Window
from etl.core.pg_load import run_sql_file
from etl.sources.shopify.raw_catalog import load_catalog
from etl.sources.shopify.shop import connect

SQL_DIR = Path(__file__).resolve().parent / "sql"
TRANSFORMS = ("catalog_products.sql", "catalog_variants.sql", "catalog_prune.sql",
              "catalog_cost_seed.sql")  # fmt: skip
CH_TTL = timedelta(days=5 * 365 + 1)  # clevrr_db products: TTL created_at + 5 years

PRODUCT_COLUMNS = (
    "product_id", "organization_id", "title", "handle", "status", "vendor", "product_type",
    "tags", "created_at", "updated_at", "image_url", "variants_count", "has_variants",
    "options_count", "default_price", "product_full_url", "handling_cost", "source_type",
    "sku_id", "cost_per_item", "default_variant_id", "collection_name",
)  # fmt: skip
VARIANT_COLUMNS = (
    "variant_id", "organization_id", "product_id", "title", "sku", "position", "price",
    "compare_at_price", "option1", "option2", "option3", "inventory_quantity",
    "inventory_item_id", "requires_shipping", "taxable", "created_at", "updated_at",
    "cost_per_item", "shopify_cost_per_item", "source_type", "is_default", "image_url",
)  # fmt: skip
_PRODUCTS: Final = (
    "SELECT product_id, organization_id, title, handle, status, vendor, product_type, tags,"
    " created_at, updated_at, image_url, variants_count, has_variants, options_count,"
    " default_price, product_full_url, handling_cost, source_type, sku_id, cost_per_item,"
    " default_variant_id, collection_name FROM products WHERE organization_id = %(org)s"
)
_VARIANTS: Final = (
    'SELECT variant_id, organization_id, product_id, title, sku, "position", price,'
    " compare_at_price, option1, option2, option3, inventory_quantity, inventory_item_id,"
    " requires_shipping, taxable, created_at, updated_at, cost_per_item, shopify_cost_per_item,"
    " source_type, is_default, image_url FROM product_variants WHERE organization_id = %(org)s"
)
_RAW_FIGURES: Final = (
    "SELECT (SELECT count(*) FROM raw_shopify_products WHERE organization_id = %(org)s),"
    " (SELECT count(*) FROM products WHERE organization_id = %(org)s),"
    " (SELECT count(DISTINCT (v ->> 'id')::bigint) FROM raw_shopify_products rp"
    "  CROSS JOIN LATERAL jsonb_array_elements(COALESCE(rp.variants, '[]'::jsonb)) AS v"
    "  WHERE rp.organization_id = %(org)s),"
    " (SELECT count(*) FROM product_variants WHERE organization_id = %(org)s)"
)
_PRODUCT_FIGURES_PG: Final = (
    "SELECT count(*), sum(default_price) FROM products"
    " WHERE organization_id = %(org)s AND created_at >= %(cutoff)s"
)
_PRODUCT_FIGURES_CH: Final = (
    "SELECT count(), sum(default_price) FROM products"
    " WHERE organization_id = {org:String} AND created_at >= {cutoff:DateTime}"
)
_VARIANT_FIGURES_PG: Final = (
    "SELECT count(*), sum(price), count(*) FILTER (WHERE is_default) FROM product_variants"
    " WHERE organization_id = %(org)s"
)
_VARIANT_FIGURES_CH: Final = (
    "SELECT count(), sum(price), countIf(is_default = 1) FROM product_variants"
    " WHERE organization_id = {org:String}"
)


def _rows(ctx: RunContext, sql: LiteralString, params: dict[str, Any]) -> list[tuple[Any, ...]]:
    with ctx.pg.connection() as conn:
        return conn.execute(sql, params).fetchall()


def extract_catalog(ctx: RunContext, org: Org, window: Window) -> StepResult:
    with connect(ctx, org) as client, ctx.pg.connection() as conn, conn.transaction():
        counts = load_catalog(conn, org.org_id, client)
    fetched = counts["products"] + counts["collections"] + counts["collects"]
    return StepResult(fetched, fetched, detail=counts)


def transform_catalog(ctx: RunContext, org: Org, window: Window) -> StepResult:
    detail: dict[str, list[int]] = {}
    with ctx.pg.connection() as conn, conn.transaction():
        for name in TRANSFORMS:
            counts = run_sql_file(conn, SQL_DIR / name, {"org": org.org_id})
            detail[name.removesuffix(".sql")] = counts
    return StepResult(detail=detail)


def sync_catalog(ctx: RunContext, org: Org, window: Window) -> StepResult:
    params = {"org": org.org_id}
    products = [list(r) for r in _rows(ctx, _PRODUCTS, params)]
    variants = [list(r) for r in _rows(ctx, _VARIANTS, params)]
    writer = ChWriter(ctx.ch, ctx.settings.clickhouse)
    written = writer.replace("products", org.org_id, Slice(), PRODUCT_COLUMNS, products)
    written += writer.replace("product_variants", org.org_id, Slice(), VARIANT_COLUMNS, variants)
    return StepResult(len(products) + len(variants), written,
                      detail={"products": len(products), "variants": len(variants)})  # fmt: skip


def check_catalog(ctx: RunContext, org: Org, window: Window) -> StepResult:
    params: dict[str, Any] = {"org": org.org_id}
    raw = _rows(ctx, _RAW_FIGURES, params)[0]
    same_figures("products (raw vs Postgres)", ("rows",), raw[0:1], raw[1:2])
    same_figures("product_variants (raw vs Postgres)", ("rows",), raw[2:3], raw[3:4])
    # Products older than the ClickHouse TTL may be gone there; compare the newer ones only.
    params["cutoff"] = (ctx.started_at - CH_TTL + timedelta(days=2)).replace(microsecond=0)
    products = same_figures(
        "products", ("rows", "price_total"), _rows(ctx, _PRODUCT_FIGURES_PG, params)[0],
        ch.rows(ctx.ch, _PRODUCT_FIGURES_CH, params)[0],
    )  # fmt: skip
    variants = same_figures(
        "product_variants", ("rows", "price_total", "defaults"),
        _rows(ctx, _VARIANT_FIGURES_PG, params)[0],
        ch.rows(ctx.ch, _VARIANT_FIGURES_CH, {"org": org.org_id})[0],
    )  # fmt: skip
    detail = {"raw_products": raw[0], "raw_variants": raw[2],
              "products": {k: str(v) for k, v in products.items()},
              "variants": {k: str(v) for k, v in variants.items()}}  # fmt: skip
    return StepResult(detail=detail)


SHOPIFY_CATALOG = Pipeline(
    name="shopify-catalog",
    source="shopify",
    steps=(
        Step("extract_catalog", extract_catalog),
        Step("transform_catalog", transform_catalog),
        Step("sync_catalog", sync_catalog),
        Step("check_catalog", check_catalog),
    ),
    lookback=timedelta(0),  # full pulls: the window is not used
    first_window=timedelta(days=1),
    timeout=timedelta(hours=1),
    schedule="30 18 * * *",  # legacy etl-all-shopify-catalog-daily: 00:00 IST
)
