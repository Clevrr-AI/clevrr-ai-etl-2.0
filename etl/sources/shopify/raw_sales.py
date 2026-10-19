"""Load Shopify orders into the raw tables, one org, one transaction per page.

Each order becomes a `raw_shopify_orders` row plus its line items, refunds, refund lines and
note attributes, in legacy's columns and shapes (`etl/load_raw_data.py`): JSON objects and
arrays as `json.dumps` text, Shopify's timestamp strings as they come. The raw order
timestamps are `timestamp without time zone`, so Postgres keeps the shop's wall-clock time
and drops the offset, as legacy did; the transforms expect that. Re-fetched rows upsert.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from psycopg import Connection

from etl.core.pg_load import upsert
from etl.sources.shopify.client import ShopifyClient

SOURCE = "shopify"
ORDER_COLUMNS = (
    "app_id", "id", "email", "created_at", "updated_at", "number", "note", "token",
    "total_price", "subtotal_price", "total_weight", "total_tax", "taxes_included", "currency",
    "financial_status", "total_discounts", "total_line_items_price", "cart_token",
    "checkout_token", "buyer_accepts_marketing", "name", "referring_site", "landing_site",
    "processed_at", "browser_ip", "order_number", "source_name", "fulfillment_status", "tags",
    "order_status_url", "billing_address", "shipping_address", "customer", "client_details",
    "discount_applications", "discount_codes", "payment_gateway_names", "presentment_currency",
    "subtotal_price_set", "test", "total_discounts_set", "total_line_items_price_set",
    "total_price_set", "total_tax_set", "total_tip_received", "current_total_discounts",
    "current_total_discounts_set", "current_total_price", "current_total_price_set",
    "current_subtotal_price", "current_subtotal_price_set", "current_total_tax",
    "current_total_tax_set", "customer_locale", "total_outstanding", "total_shipping_price_set",
    "fulfillments", "organization_id", "source_type", "__hevo__ingested_at",
    "__hevo__marked_deleted", "phone", "user_id", "cancelled_at", "cancel_reason", "closed_at",
    "location_id", "date_start", "note_attributes",
)  # fmt: skip
LINE_COLUMNS = (
    "id", "variant_id", "title", "quantity", "price", "grams", "sku", "variant_title", "vendor",
    "product_id", "requires_shipping", "taxable", "gift_card", "name",
    "variant_inventory_management", "fulfillable_quantity", "total_discount",
    "fulfillment_status", "fulfillment_service", "custom", "tax_lines", "price_set",
    "total_discount_set", "discount_allocations", "duties", "product_exists",
    "admin_graphql_api_id", "current_quantity", "order_id", "organization_id", "source_type",
    "__hevo__ingested_at", "__hevo__marked_deleted", "properties", "date_start",
)  # fmt: skip
REFUND_COLUMNS = (
    "id", "order_id", "created_at", "note", "user_id", "processed_at", "duties",
    "order_adjustments", "admin_graphql_api_id", "restock", "total_duties_set",
    "organization_id", "source_type", "__hevo__ingested_at", "__hevo__marked_deleted",
    "date_start",
)  # fmt: skip
REFUND_LINE_COLUMNS = (
    "id", "quantity", "line_item_id", "restock_type", "subtotal", "total_tax", "line_item",
    "total_tax_set", "order_id", "refund_id", "organization_id", "source_type",
    "__hevo__ingested_at", "__hevo__marked_deleted", "location_id", "date_start",
)  # fmt: skip
NOTE_COLUMNS = (
    "order_id", "name", "value", "organization_id", "source_type", "__hevo__ingested_at",
    "__hevo__marked_deleted", "date_start",
)  # fmt: skip
TABLES: tuple[tuple[str, Sequence[str], Sequence[str]], ...] = (
    ("raw_shopify_orders", ORDER_COLUMNS, ("id", "organization_id")),
    ("raw_shopify_order_lines", LINE_COLUMNS, ("id", "organization_id")),
    ("raw_shopify_refunds", REFUND_COLUMNS, ("id", "organization_id")),
    ("raw_shopify_order_refund_lines", REFUND_LINE_COLUMNS, ("id", "organization_id")),
    ("raw_shopify_order_note_attributes", NOTE_COLUMNS, ("name", "order_id")),  # legacy's key
)


def _json(value: Any) -> Any:
    return json.dumps(value) if isinstance(value, dict | list) else value


def _row(record: Mapping[str, Any], columns: Sequence[str]) -> list[Any]:
    return [_json(record.get(c)) for c in columns]


def _items(value: Any) -> list[Mapping[str, Any]]:
    """A nested Shopify list (line_items, refunds ...); absent or null is empty."""
    return list(value) if isinstance(value, list) else []  # pyright: ignore[reportUnknownArgumentType]


@dataclass
class RawRows:
    """One page of orders, split into the five raw tables' rows."""

    by_table: dict[str, list[list[Any]]] = field(
        default_factory=lambda: {name: [] for name, _, _ in TABLES}
    )

    def add(self, org_id: str, order: Mapping[str, Any]) -> None:
        tag = {"organization_id": org_id, "source_type": SOURCE}
        order_id = order.get("id")
        self.by_table["raw_shopify_orders"].append(_row({**order, **tag}, ORDER_COLUMNS))
        for item in _items(order.get("line_items")):
            row = _row({**item, "order_id": order_id, **tag}, LINE_COLUMNS)
            self.by_table["raw_shopify_order_lines"].append(row)
        for refund in _items(order.get("refunds")):
            row = _row({**refund, "order_id": order_id, **tag}, REFUND_COLUMNS)
            self.by_table["raw_shopify_refunds"].append(row)
            for line in _items(refund.get("refund_line_items")):
                record = {**line, "refund_id": refund.get("id"), "order_id": order_id, **tag}
                self.by_table["raw_shopify_order_refund_lines"].append(
                    _row(record, REFUND_LINE_COLUMNS)
                )
        for note in _items(order.get("note_attributes")):
            record = {"order_id": order_id, "name": note.get("name"), "value": note.get("value"),
                      "date_start": order.get("date_start"), **tag}  # fmt: skip
            self.by_table["raw_shopify_order_note_attributes"].append(_row(record, NOTE_COLUMNS))


def load_orders(conn: Connection[Any], org_id: str, client: ShopifyClient,
                passes: Iterable[Mapping[str, Any]]) -> dict[str, int]:  # fmt: skip
    """Fetch every page of each pass (a set of `orders` params) and upsert it, committing per
    page. Returns rows written per table and the pages read."""
    counts = {name: 0 for name, _, _ in TABLES} | {"pages": 0}
    for params in passes:
        for page in client.pages("orders", params, "orders"):
            rows = RawRows()
            for order in page:
                rows.add(org_id, order)
            with conn.transaction():
                for name, columns, key in TABLES:
                    counts[name] += upsert(conn, name, columns, key, rows.by_table[name])
            counts["pages"] += 1
    return counts
