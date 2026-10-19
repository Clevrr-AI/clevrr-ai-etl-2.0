"""Legacy's reliability report: the Shopify metric definitions, copied unchanged from
app/scripts/data_reliability_report.py CONNECTOR_METRICS["shopify"] (its f-strings are
written as concatenation). A builder file: SQL from fixed constants only."""

# ruff: noqa: E501
# pyright: reportUnknownLambdaType=false, reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false
from __future__ import annotations

from typing import Any

# -- Shared SQL fragments for sanitized tables --
_SHOPIFY_ORDER_LINES_JOIN = """
    FROM order_lines ol
    JOIN orders o ON o.order_id = ol.order_id AND o.organization_id = ol.organization_id
    WHERE ol.organization_id = %(org_id)s
      AND o.created_at >= %(start)s AND o.created_at < %(end)s
"""

_CKH_ORDER_LINES_WHERE = """
    FROM {db}.order_lines FINAL
    WHERE organization_id = '{{org_id}}'
      AND created_at >= toDateTime('{{start}}') AND created_at < toDateTime('{{end}}')
"""

# -- Shared SQL fragments for raw tables --
_RAW_ORDER_LINES_JOIN = """
    FROM raw_shopify_order_lines rol
    JOIN raw_shopify_orders ro ON ro.id::TEXT = rol.order_id::TEXT
         AND ro.organization_id = rol.organization_id
    WHERE ro.organization_id = %(org_id)s
      AND ro.created_at >= %(start)s AND ro.created_at < %(end)s
"""

_RAW_REFUND_LINES_JOIN = """
    FROM raw_shopify_order_refund_lines rorl
    JOIN raw_shopify_refunds rr ON rr.id::TEXT = rorl.refund_id::TEXT
         AND rr.organization_id = rorl.organization_id
    WHERE rorl.organization_id = %(org_id)s
      AND rr.created_at >= %(start)s AND rr.created_at < %(end)s
"""

_RAW_REFUNDS_WHERE = """
    FROM raw_shopify_refunds rr
    WHERE rr.organization_id = %(org_id)s
      AND rr.created_at >= %(start)s AND rr.created_at < %(end)s
"""

# -- Common PSQL WHERE for sanitized orders/refunds --
_PSQL_ORG_DATE = "organization_id = %(org_id)s AND created_at >= %(start)s AND created_at < %(end)s"

# -- Common CKH WHERE --
_CKH_ORG_DATE = "organization_id = '{{org_id}}' AND created_at >= toDateTime('{{start}}') AND created_at < toDateTime('{{end}}')"


SHOPIFY_METRICS: dict[str, Any] = {
    "sources": ["shopify"],
    "display_order": [
        "total_orders",
        "gross_sales",
        "net_sales",
        "total_sales",
        "total_returns",
        "discounts",
        "tax",
        "shipping_price",
    ],
    "metrics": {
        # --- total_orders ---
        "total_orders": {
            "psql_sql": (
                "SELECT COUNT(DISTINCT ol.order_id) AS value " + _SHOPIFY_ORDER_LINES_JOIN
            ),
            "ckh_sql": ("SELECT countDistinct(order_id) AS value " + _CKH_ORDER_LINES_WHERE),
            "raw_sql": ("SELECT COUNT(DISTINCT rol.order_id) AS value " + _RAW_ORDER_LINES_JOIN),
            "dashboard_metric": "total_orders",
        },
        # --- raw_gross_sales (internal, used by gross_sales composite) ---
        "raw_gross_sales": {
            "psql_sql": (
                "SELECT COALESCE(SUM(ol.quantity * ol.item_price), 0) AS value "
                + _SHOPIFY_ORDER_LINES_JOIN
            ),
            "ckh_sql": (
                "SELECT COALESCE(SUM(quantity * item_price), 0) AS value " + _CKH_ORDER_LINES_WHERE
            ),
            "raw_sql": (
                "SELECT COALESCE(SUM(rol.quantity::NUMERIC * rol.price::NUMERIC), 0) AS value "
                + _RAW_ORDER_LINES_JOIN
            ),
            "dashboard_metric": "raw_gross_sales",
            "internal": True,
        },
        # --- raw_order_tax (internal, used by gross_sales composite) ---
        "raw_order_tax": {
            "psql_sql": (
                "SELECT COALESCE(SUM(ol.tax_allocated), 0) AS value " + _SHOPIFY_ORDER_LINES_JOIN
            ),
            "ckh_sql": (
                "SELECT COALESCE(SUM(tax_allocated), 0) AS value " + _CKH_ORDER_LINES_WHERE
            ),
            "raw_sql": (
                """SELECT COALESCE(SUM(
                    (SELECT COALESCE(SUM((elem->>'price')::NUMERIC), 0)
                     FROM jsonb_array_elements(COALESCE(rol.tax_lines::jsonb, '[]'::jsonb)) elem)
                ), 0) AS value """
                + _RAW_ORDER_LINES_JOIN
            ),
            "dashboard_metric": "raw_order_tax",
            "internal": True,
        },
        # --- discounts ---
        "discounts": {
            "psql_sql": (
                "SELECT COALESCE(SUM(ol.discount_allocated), 0) AS value "
                + _SHOPIFY_ORDER_LINES_JOIN
            ),
            "ckh_sql": (
                "SELECT COALESCE(SUM(discount_allocated), 0) AS value " + _CKH_ORDER_LINES_WHERE
            ),
            "raw_sql": (
                """SELECT COALESCE(SUM(
                    (SELECT COALESCE(SUM((elem->>'amount')::NUMERIC), 0)
                     FROM jsonb_array_elements(COALESCE(rol.discount_allocations::jsonb, '[]'::jsonb)) elem)
                ), 0) AS value """
                + _RAW_ORDER_LINES_JOIN
            ),
            "dashboard_metric": "discounts",
        },
        # --- total_returns (refund lines net of tax + signed non-shipping adjustments) ---
        # Mirrors the dashboard RETURNS formula (metric_config.py): adjustments keep
        # their Shopify sign (negative = extra money out -> MORE returns). The old
        # ABS() version diverged from the dashboard by 2x on negative adjustments.
        "total_returns": {
            "psql_sql": """
                    SELECT (
                        (SELECT COALESCE(SUM(COALESCE(subtotal_amount, 0) - COALESCE(total_tax, 0)), 0)
                         FROM refund_lines
                         WHERE organization_id = %(org_id)s
                           AND created_at >= %(start)s AND created_at < %(end)s)
                      + (SELECT COALESCE(SUM(CASE WHEN kind IS NULL OR kind != 'shipping_refund'
                                                  THEN -COALESCE(refund_amount, 0) ELSE 0 END), 0)
                         FROM refunds
                         WHERE organization_id = %(org_id)s
                           AND created_at >= %(start)s AND created_at < %(end)s)
                    ) AS value
                """,
            "ckh_sql": """
                    SELECT (
                        (SELECT COALESCE(SUM(COALESCE(subtotal_amount, 0) - COALESCE(total_tax, 0)), 0)
                         FROM {db}.refund_lines FINAL
                         WHERE organization_id = '{{org_id}}'
                           AND created_at >= toDateTime('{{start}}') AND created_at < toDateTime('{{end}}'))
                      + (SELECT COALESCE(SUM(if((kind IS NULL) OR (kind != 'shipping_refund'),
                                                -COALESCE(refund_amount, 0), 0)), 0)
                         FROM {db}.refunds FINAL
                         WHERE organization_id = '{{org_id}}'
                           AND created_at >= toDateTime('{{start}}') AND created_at < toDateTime('{{end}}'))
                    ) AS value
                """,
            "raw_sql": """
                    SELECT (
                        (SELECT COALESCE(SUM(COALESCE(rorl.subtotal::NUMERIC, 0)
                                             - COALESCE(rorl.total_tax::NUMERIC, 0)), 0)
                         FROM raw_shopify_order_refund_lines rorl
                         JOIN raw_shopify_refunds rr
                           ON rr.id::TEXT = rorl.refund_id::TEXT
                          AND rr.organization_id = rorl.organization_id
                         WHERE rorl.organization_id = %(org_id)s
                           AND rr.created_at >= %(start)s AND rr.created_at < %(end)s)
                      + (SELECT COALESCE(SUM(-NULLIF((adj.elem ->> 'amount'), '')::NUMERIC), 0)
                         FROM raw_shopify_refunds rr
                         LEFT JOIN LATERAL jsonb_array_elements(
                             COALESCE(rr.order_adjustments::jsonb, '[]'::jsonb)) adj(elem) ON TRUE
                         WHERE rr.organization_id = %(org_id)s
                           AND rr.created_at >= %(start)s AND rr.created_at < %(end)s
                           AND ((adj.elem ->> 'kind') IS NULL OR (adj.elem ->> 'kind') != 'shipping_refund'))
                    ) AS value
                """,
            "dashboard_metric": "total_returns",
        },
        # --- tax (multi-table: orders.tax_price - refund_lines.total_tax + refunds.shipping_refund_tax) ---
        # Uses SHOPIFY_SINGLE_STAT_OVERRIDES formula from metric_dispatcher.py
        "tax": {
            "psql_sql": (
                """
                    SELECT (
                        COALESCE((SELECT SUM(COALESCE(tax_price, 0)) FROM orders
                                  WHERE """
                + _PSQL_ORG_DATE
                + """), 0)
                        - COALESCE((SELECT SUM(total_tax) FROM refund_lines
                                    WHERE """
                + _PSQL_ORG_DATE
                + """), 0)
                        + COALESCE((SELECT SUM(COALESCE(shipping_refund_tax, 0)) FROM refunds
                                    WHERE """
                + _PSQL_ORG_DATE
                + """), 0)
                    ) AS value
                """
            ),
            "ckh_sql": (
                """
                    SELECT (
                        (SELECT COALESCE(SUM(COALESCE(tax_price, 0)), 0) FROM {db}.orders FINAL
                         WHERE """
                + _CKH_ORG_DATE
                + """)
                        - (SELECT COALESCE(SUM(total_tax), 0) FROM {db}.refund_lines FINAL
                           WHERE """
                + _CKH_ORG_DATE
                + """)
                        + (SELECT COALESCE(SUM(COALESCE(shipping_refund_tax, 0)), 0) FROM {db}.refunds FINAL
                           WHERE """
                + _CKH_ORG_DATE
                + """)
                    ) AS value
                """
            ),
            "raw_sql": (
                """
                    SELECT (
                        COALESCE((SELECT SUM(ro.total_tax::NUMERIC)
                                  FROM raw_shopify_orders ro
                                  WHERE ro."""
                + _PSQL_ORG_DATE
                + """), 0)
                        - COALESCE((SELECT SUM(rorl.total_tax::NUMERIC)
                                    FROM raw_shopify_order_refund_lines rorl
                                    JOIN raw_shopify_refunds rr
                                      ON rr.id::TEXT = rorl.refund_id::TEXT
                                     AND rr.organization_id = rorl.organization_id
                                    WHERE rorl."""
                + _PSQL_ORG_DATE
                + """), 0)
                        + COALESCE((SELECT SUM(COALESCE(NULLIF((adj.elem ->> 'tax_amount'), '')::NUMERIC, 0))
                                    FROM raw_shopify_refunds rr
                                    LEFT JOIN LATERAL jsonb_array_elements(
                                        COALESCE(rr.order_adjustments::jsonb, '[]'::jsonb)) adj(elem) ON TRUE
                                    WHERE rr."""
                + _PSQL_ORG_DATE
                + """
                                      AND (adj.elem ->> 'kind') = 'shipping_refund'), 0)
                    ) AS value
                """
            ),
            "dashboard_metric": "tax",
        },
        # --- shipping_price (multi-table: orders.shipping_price + refunds.refund_amount[shipping_refund]
        #     - orders.tax_price + order_lines.tax_allocated) ---
        # Uses SHOPIFY_SINGLE_STAT_OVERRIDES formula from metric_dispatcher.py
        "shipping_price": {
            "psql_sql": (
                """
                    SELECT (
                        COALESCE((SELECT SUM(COALESCE(shipping_price, 0)) FROM orders
                                  WHERE """
                + _PSQL_ORG_DATE
                + """), 0)
                        + COALESCE((SELECT SUM(COALESCE(shipping_refund, 0)) FROM refunds
                                    WHERE """
                + _PSQL_ORG_DATE
                + """), 0)
                        - COALESCE((SELECT SUM(COALESCE(tax_price, 0)) FROM orders
                                    WHERE """
                + _PSQL_ORG_DATE
                + """), 0)
                        + COALESCE((SELECT SUM(COALESCE(ol.tax_allocated, 0))
                                    """
                + _SHOPIFY_ORDER_LINES_JOIN
                + """), 0)
                    ) AS value
                """
            ),
            "ckh_sql": (
                """
                    SELECT (
                        (SELECT COALESCE(SUM(COALESCE(shipping_price, 0)), 0) FROM {db}.orders FINAL
                         WHERE """
                + _CKH_ORG_DATE
                + """)
                        + (SELECT COALESCE(SUM(COALESCE(shipping_refund, 0)), 0) FROM {db}.refunds FINAL
                           WHERE """
                + _CKH_ORG_DATE
                + """)
                        - (SELECT COALESCE(SUM(COALESCE(tax_price, 0)), 0) FROM {db}.orders FINAL
                           WHERE """
                + _CKH_ORG_DATE
                + """)
                        + (SELECT COALESCE(SUM(COALESCE(tax_allocated, 0)), 0) FROM {db}.order_lines FINAL
                           WHERE """
                + _CKH_ORG_DATE
                + """)
                    ) AS value
                """
            ),
            "raw_sql": (
                """
                    SELECT (
                        COALESCE((SELECT SUM((ro.total_shipping_price_set::jsonb->'shop_money'->>'amount')::NUMERIC)
                                  FROM raw_shopify_orders ro
                                  WHERE ro."""
                + _PSQL_ORG_DATE
                + """), 0)
                        + COALESCE((SELECT SUM(NULLIF((adj.elem ->> 'amount'), '')::NUMERIC)
                                    FROM raw_shopify_refunds rr
                                    LEFT JOIN LATERAL jsonb_array_elements(
                                        COALESCE(rr.order_adjustments::jsonb, '[]'::jsonb)) adj(elem) ON TRUE
                                    WHERE rr."""
                + _PSQL_ORG_DATE
                + """
                                      AND (adj.elem ->> 'kind') = 'shipping_refund'), 0)
                        - COALESCE((SELECT SUM(ro.total_tax::NUMERIC)
                                    FROM raw_shopify_orders ro
                                    WHERE ro."""
                + _PSQL_ORG_DATE
                + """), 0)
                        + COALESCE((SELECT SUM(
                              (SELECT COALESCE(SUM((elem->>'price')::NUMERIC), 0)
                               FROM jsonb_array_elements(COALESCE(rol.tax_lines::jsonb, '[]'::jsonb)) elem))
                                    FROM raw_shopify_order_lines rol
                                    JOIN raw_shopify_orders ro2
                                      ON ro2.id::TEXT = rol.order_id::TEXT
                                     AND ro2.organization_id = rol.organization_id
                                    WHERE ro2."""
                + _PSQL_ORG_DATE
                + """), 0)
                    ) AS value
                """
            ),
            "dashboard_metric": "shipping_price",
        },
    },
    # IMPORTANT: composites are computed in insertion order.
    # gross_sales MUST come before net_sales, net_sales before total_sales.
    "composites": {
        "gross_sales": {
            "deps": ["raw_gross_sales", "raw_order_tax"],
            "fn": lambda r: round(
                (r.get("raw_gross_sales") or 0) - (r.get("raw_order_tax") or 0), 2
            ),
            "dashboard_metric": "gross_sales",
        },
        "net_sales": {
            "deps": ["gross_sales", "total_returns", "discounts"],
            "fn": lambda r: round(
                (r.get("gross_sales") or 0)
                - (r.get("total_returns") or 0)
                - (r.get("discounts") or 0),
                2,
            ),
            "dashboard_metric": "net_sales",
        },
        "total_sales": {
            "deps": ["net_sales", "tax", "shipping_price"],
            "fn": lambda r: round(
                (r.get("net_sales") or 0) + (r.get("tax") or 0) + (r.get("shipping_price") or 0), 2
            ),
            "dashboard_metric": "total_sales",
        },
    },
}
