"""Load the Shopify catalog into the raw tables, one org, in the caller's transaction.

Rows keep legacy's shapes (`etl/load_raw_data.py`): JSON objects and arrays as `json.dumps`
text, Shopify's timestamp strings as they come (the columns are timestamptz, so the offset is
kept). Each endpoint is a full pull. After the last page the org's rows that Shopify no longer
returned are deleted, so the raw tables hold the current catalog. Legacy wiped them first and
then fetched only the collects updated the day before.

An empty products response for an org that had products fails the step instead of deleting
the catalog.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final, LiteralString

from psycopg import Connection

from etl.core.model import StepFailed
from etl.core.pg_load import upsert
from etl.sources.shopify.client import ShopifyClient

SOURCE = "shopify"
PRODUCT_COLUMNS = (
    "id", "organization_id", "admin_graphql_api_id", "title", "handle", "status", "vendor",
    "product_type", "tags", "image", "images", "variants", "options", "created_at", "updated_at",
)  # fmt: skip
COLLECT_COLUMNS = ("organization_id", "source_type", "product_id", "collection_id", "updated_at")
COLLECTION_COLUMNS = (
    "organization_id", "source_type", "collection_id", "collection_type", "title", "handle",
    "body_html", "published_at", "updated_at", "published_scope", "admin_graphql_api_id",
    "sort_order", "disjunctive", "rules", "raw_payload",
)  # fmt: skip
# The params legacy sent (its connector_endpoints row and fallbacks; `status=any` dropped).
PRODUCTS_PARAMS = {"limit": 250}
COLLECTS_PARAMS = {"limit": 250, "fields": "product_id,collection_id,updated_at"}
CUSTOM_PARAMS = {"limit": 250, "fields": "id,title,handle,published_at,updated_at,"
                 "published_scope,admin_graphql_api_id"}  # fmt: skip
SMART_PARAMS = {"limit": 250, "fields": "id,title,handle,published_at,updated_at,"
                "published_scope,disjunctive,admin_graphql_api_id"}  # fmt: skip

_COUNT_PRODUCTS: Final = "SELECT count(*) FROM raw_shopify_products WHERE organization_id = %(org)s"
_PRUNE_PRODUCTS: Final = (
    "DELETE FROM raw_shopify_products WHERE organization_id = %(org)s AND NOT (id = ANY(%(ids)s))"
)
_PRUNE_COLLECTIONS: Final = (
    "DELETE FROM raw_shopify_collections"
    " WHERE organization_id = %(org)s AND NOT (collection_id = ANY(%(ids)s))"
)
_PRUNE_COLLECTS: Final = (
    "DELETE FROM raw_shopify_collects rc WHERE rc.organization_id = %(org)s AND NOT EXISTS"
    " (SELECT 1 FROM unnest(%(products)s::bigint[], %(collections)s::bigint[]) AS k(p, c)"
    " WHERE k.p = rc.product_id AND k.c = rc.collection_id)"
)


def _json(value: Any) -> Any:
    return json.dumps(value) if isinstance(value, dict | list) else value


def product_row(org_id: str, p: Mapping[str, Any]) -> list[Any]:
    values = {**p, "organization_id": org_id}
    return [_json(values.get(c)) for c in PRODUCT_COLUMNS]


def collect_row(org_id: str, c: Mapping[str, Any]) -> list[Any]:
    return [org_id, SOURCE, c.get("product_id"), c.get("collection_id"), c.get("updated_at")]


def collection_row(org_id: str, kind: str, c: Mapping[str, Any]) -> list[Any]:
    return [org_id, SOURCE, c.get("id") or c.get("collection_id"), kind, c.get("title"),
            c.get("handle"), c.get("body_html"), c.get("published_at"), c.get("updated_at"),
            c.get("published_scope"), c.get("admin_graphql_api_id"), c.get("sort_order"),
            c.get("disjunctive"), _json(c.get("rules")), json.dumps(c)]  # fmt: skip


def _load(conn: Connection[Any], client: ShopifyClient, endpoint: str, params: Mapping[str, Any],
          to_row: Callable[[Mapping[str, Any]], list[Any]], table: str, columns: Sequence[str],
          key: Sequence[str]) -> list[tuple[Any, ...]]:  # fmt: skip
    """Upsert every page of `endpoint`; return the key of each row written (not the rows: a
    big catalog's JSON stays out of memory)."""
    at = [columns.index(k) for k in key]
    keys: list[tuple[Any, ...]] = []
    for page in client.pages(endpoint, params, endpoint):
        batch = [to_row(item) for item in page]
        upsert(conn, table, columns, key, batch)
        keys.extend(tuple(row[i] for i in at) for row in batch)
    return keys


def _execute(conn: Connection[Any], sql: LiteralString, params: Mapping[str, Any]) -> int:
    return conn.execute(sql, dict(params)).rowcount


def load_catalog(conn: Connection[Any], org_id: str, client: ShopifyClient) -> dict[str, int]:
    """Make the org's raw catalog tables equal to Shopify's current catalog. Returns counts."""
    before_row = conn.execute(_COUNT_PRODUCTS, {"org": org_id}).fetchone()
    before = int(before_row[0]) if before_row else 0
    products = _load(conn, client, "products", PRODUCTS_PARAMS,
                     lambda p: product_row(org_id, p), "raw_shopify_products", PRODUCT_COLUMNS,
                     ("id", "organization_id"))  # fmt: skip
    if not products and before:
        raise StepFailed(f"Shopify returned no products; {before} are stored. Nothing deleted.",
                         detail={"stored_products": before})  # fmt: skip
    collections = [
        *_load(conn, client, "custom_collections", CUSTOM_PARAMS,
               lambda c: collection_row(org_id, "custom", c), "raw_shopify_collections",
               COLLECTION_COLUMNS, ("organization_id", "collection_id")),
        *_load(conn, client, "smart_collections", SMART_PARAMS,
               lambda c: collection_row(org_id, "smart", c), "raw_shopify_collections",
               COLLECTION_COLUMNS, ("organization_id", "collection_id")),
    ]  # fmt: skip
    collects = _load(conn, client, "collects", COLLECTS_PARAMS, lambda c: collect_row(org_id, c),
                     "raw_shopify_collects", COLLECT_COLUMNS,
                     ("organization_id", "product_id", "collection_id"))  # fmt: skip
    pruned = {  # keys: products (id, org), collections (org, id), collects (org, p, c)
        "products": _execute(conn, _PRUNE_PRODUCTS,
                             {"org": org_id, "ids": [k[0] for k in products]}),
        "collections": _execute(conn, _PRUNE_COLLECTIONS,
                                {"org": org_id, "ids": [k[1] for k in collections]}),
        "collects": _execute(conn, _PRUNE_COLLECTS, {"org": org_id,
                             "products": [k[1] for k in collects],
                             "collections": [k[2] for k in collects]}),
    }  # fmt: skip
    return {"products": len(products), "collections": len(collections),
            "collects": len(collects), **{f"pruned_{k}": v for k, v in pruned.items()}}  # fmt: skip
