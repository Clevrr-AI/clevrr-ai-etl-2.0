"""Legacy's reliability report definitions (clevrr-ai-server app/scripts/
data_reliability_report.py, PIPELINE_COUNT_CONFIG and CONNECTOR_METRICS), copied unchanged;
the Shopify metrics are in sql_reliability_shopify.py, and legacy's f-strings are written as
concatenation.

A builder file: the SQL below is assembled from these fixed constants only. Postgres queries
bind %(org_id)s, %(start)s, %(end)s. The ClickHouse templates keep legacy's text placeholders
({db}, {{org_id}}, {{start}}, {{end}}); `ch_query` turns them into bound parameters.
"""

# ruff: noqa: E501
# pyright: reportUnknownLambdaType=false, reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false
from __future__ import annotations

from typing import Any

from etl.checks.sql_reliability_shopify import SHOPIFY_METRICS

# ---------------------------------------------------------------------------
# Pipeline count config — shows record count at each stage for the primary entity
# ---------------------------------------------------------------------------

PIPELINE_COUNT_CONFIG: dict[str, dict[str, Any]] = {
    "shopify": {
        "label": "Orders",
        "raw_sql": """SELECT COUNT(*) AS value FROM raw_shopify_orders
                      WHERE organization_id = %(org_id)s
                        AND created_at >= %(start)s AND created_at < %(end)s""",
        "psql_sql": """SELECT COUNT(*) AS value FROM orders
                       WHERE organization_id = %(org_id)s
                         AND created_at >= %(start)s AND created_at < %(end)s""",
        "ckh_sql": """SELECT count() AS value FROM {db}.orders FINAL
                      WHERE organization_id = '{{org_id}}'
                        AND created_at >= toDateTime('{{start}}') AND created_at < toDateTime('{{end}}')""",
        "dashboard_metric": "total_orders",
    },
    "meta_ads": {
        "label": "Ad Insights",
        "raw_sql": """SELECT COUNT(*) AS value FROM raw_meta_ad_insights
                      WHERE organization_id = %(org_id)s
                        AND date_start >= %(start)s AND date_start < %(end)s""",
        "psql_sql": """SELECT COUNT(*) AS value FROM advertising
                       WHERE organization_id = %(org_id)s AND source_type = 'meta_ads'
                         AND created_at >= %(start)s AND created_at < %(end)s""",
        "ckh_sql": """SELECT count() AS value FROM {db}.advertising
                      WHERE organization_id = '{{org_id}}' AND source_type = 'meta_ads'
                        AND created_at >= toDateTime('{{start}}') AND created_at < toDateTime('{{end}}')""",
    },
    "google_ads": {
        "label": "Ad Records",
        "raw_sql": """SELECT COUNT(*) AS value FROM raw_google_ads_ad_group_ad
                      WHERE organization_id = %(org_id)s
                        AND __hevo_report_date >= %(start)s AND __hevo_report_date < %(end)s""",
        "psql_sql": """SELECT COUNT(*) AS value FROM advertising
                       WHERE organization_id = %(org_id)s AND source_type = 'google_ads'
                         AND created_at >= %(start)s AND created_at < %(end)s""",
        "ckh_sql": """SELECT count() AS value FROM {db}.advertising
                      WHERE organization_id = '{{org_id}}' AND source_type = 'google_ads'
                        AND created_at >= toDateTime('{{start}}') AND created_at < toDateTime('{{end}}')""",
    },
    "delhivery": {
        "label": "Shipments",
        "raw_sql": """SELECT COUNT(*) AS value FROM raw_shipping_tracking
                      WHERE organization_id = %(org_id)s
                        AND fetched_at >= %(start)s AND fetched_at < %(end)s""",
        "psql_sql": """SELECT COUNT(*) AS value FROM shipments
                       WHERE organization_id = %(org_id)s
                         AND created_at >= %(start)s AND created_at < %(end)s""",
        "ckh_sql": """SELECT count() AS value FROM {db}.shipments FINAL
                      WHERE organization_id = '{{org_id}}'
                        AND created_at >= toDateTime('{{start}}') AND created_at < toDateTime('{{end}}')""",
    },
}

# ---------------------------------------------------------------------------
# Metric definitions per connector
# Each metric has: psql_sql, ckh_sql, raw_sql (optional), dashboard_metric.
# raw_sql computes the metric directly from raw_shopify_* tables.
# Composites are computed in insertion order from base metric results.
# ---------------------------------------------------------------------------


CONNECTOR_METRICS: dict[str, dict[str, Any]] = {
    "shopify": SHOPIFY_METRICS,
    # -- Meta Ads --
    "meta_ads": {
        "sources": ["meta_ads"],
        "source_type_filter": "meta_ads",
        "display_order": ["total_ad_spend", "impressions", "clicks", "total_ad_revenue"],
        "metrics": {
            "total_ad_spend": {
                "psql_sql": """SELECT COALESCE(SUM(spend), 0) AS value FROM advertising
                    WHERE organization_id = %(org_id)s AND source_type = 'meta_ads'
                      AND created_at >= %(start)s AND created_at < %(end)s""",
                "ckh_sql": """SELECT COALESCE(SUM(spend), 0) AS value FROM {db}.advertising
                    WHERE organization_id = '{{org_id}}' AND source_type = 'meta_ads'
                      AND created_at >= toDateTime('{{start}}') AND created_at < toDateTime('{{end}}')""",
                "dashboard_metric": "total_ad_spend",
            },
            "impressions": {
                "psql_sql": """SELECT COALESCE(SUM(impressions), 0) AS value FROM advertising
                    WHERE organization_id = %(org_id)s AND source_type = 'meta_ads'
                      AND created_at >= %(start)s AND created_at < %(end)s""",
                "ckh_sql": """SELECT COALESCE(SUM(impressions), 0) AS value FROM {db}.advertising
                    WHERE organization_id = '{{org_id}}' AND source_type = 'meta_ads'
                      AND created_at >= toDateTime('{{start}}') AND created_at < toDateTime('{{end}}')""",
                "dashboard_metric": "impressions",
            },
            "clicks": {
                "psql_sql": """SELECT COALESCE(SUM(clicks), 0) AS value FROM advertising
                    WHERE organization_id = %(org_id)s AND source_type = 'meta_ads'
                      AND created_at >= %(start)s AND created_at < %(end)s""",
                "ckh_sql": """SELECT COALESCE(SUM(clicks), 0) AS value FROM {db}.advertising
                    WHERE organization_id = '{{org_id}}' AND source_type = 'meta_ads'
                      AND created_at >= toDateTime('{{start}}') AND created_at < toDateTime('{{end}}')""",
                "dashboard_metric": "clicks",
            },
            "total_ad_revenue": {
                "psql_sql": """SELECT COALESCE(SUM(revenue), 0) AS value FROM advertising
                    WHERE organization_id = %(org_id)s AND source_type = 'meta_ads'
                      AND created_at >= %(start)s AND created_at < %(end)s""",
                "ckh_sql": """SELECT COALESCE(SUM(revenue), 0) AS value FROM {db}.advertising
                    WHERE organization_id = '{{org_id}}' AND source_type = 'meta_ads'
                      AND created_at >= toDateTime('{{start}}') AND created_at < toDateTime('{{end}}')""",
                "dashboard_metric": "total_ad_revenue",
            },
        },
        "composites": {},
    },
    # -- Google Ads --
    "google_ads": {
        "sources": ["google_ads"],
        "source_type_filter": "google_ads",
        "display_order": ["total_ad_spend", "impressions", "clicks", "total_ad_revenue"],
        "metrics": {
            "total_ad_spend": {
                "psql_sql": """SELECT COALESCE(SUM(spend), 0) AS value FROM advertising
                    WHERE organization_id = %(org_id)s AND source_type = 'google_ads'
                      AND created_at >= %(start)s AND created_at < %(end)s""",
                "ckh_sql": """SELECT COALESCE(SUM(spend), 0) AS value FROM {db}.advertising
                    WHERE organization_id = '{{org_id}}' AND source_type = 'google_ads'
                      AND created_at >= toDateTime('{{start}}') AND created_at < toDateTime('{{end}}')""",
                "dashboard_metric": "total_ad_spend",
            },
            "impressions": {
                "psql_sql": """SELECT COALESCE(SUM(impressions), 0) AS value FROM advertising
                    WHERE organization_id = %(org_id)s AND source_type = 'google_ads'
                      AND created_at >= %(start)s AND created_at < %(end)s""",
                "ckh_sql": """SELECT COALESCE(SUM(impressions), 0) AS value FROM {db}.advertising
                    WHERE organization_id = '{{org_id}}' AND source_type = 'google_ads'
                      AND created_at >= toDateTime('{{start}}') AND created_at < toDateTime('{{end}}')""",
                "dashboard_metric": "impressions",
            },
            "clicks": {
                "psql_sql": """SELECT COALESCE(SUM(clicks), 0) AS value FROM advertising
                    WHERE organization_id = %(org_id)s AND source_type = 'google_ads'
                      AND created_at >= %(start)s AND created_at < %(end)s""",
                "ckh_sql": """SELECT COALESCE(SUM(clicks), 0) AS value FROM {db}.advertising
                    WHERE organization_id = '{{org_id}}' AND source_type = 'google_ads'
                      AND created_at >= toDateTime('{{start}}') AND created_at < toDateTime('{{end}}')""",
                "dashboard_metric": "clicks",
            },
            "total_ad_revenue": {
                "psql_sql": """SELECT COALESCE(SUM(revenue), 0) AS value FROM advertising
                    WHERE organization_id = %(org_id)s AND source_type = 'google_ads'
                      AND created_at >= %(start)s AND created_at < %(end)s""",
                "ckh_sql": """SELECT COALESCE(SUM(revenue), 0) AS value FROM {db}.advertising
                    WHERE organization_id = '{{org_id}}' AND source_type = 'google_ads'
                      AND created_at >= toDateTime('{{start}}') AND created_at < toDateTime('{{end}}')""",
                "dashboard_metric": "total_ad_revenue",
            },
        },
        "composites": {},
    },
    # -- Delhivery (fulfillment) --
    "delhivery": {
        "sources": ["delhivery"],
        "display_order": ["shipment_count"],
        "metrics": {
            "shipment_count": {
                "psql_sql": """SELECT COUNT(*) AS value FROM shipments
                    WHERE organization_id = %(org_id)s
                      AND created_at >= %(start)s AND created_at < %(end)s""",
                "ckh_sql": """SELECT count() AS value FROM {db}.shipments FINAL
                    WHERE organization_id = '{{org_id}}'
                      AND created_at >= toDateTime('{{start}}') AND created_at < toDateTime('{{end}}')""",
                "dashboard_metric": None,  # no single_stat metric for this
            },
        },
        "composites": {},
    },
}


def ch_query(template: str) -> str:
    """Legacy's format_ckh_sql, with values bound instead of pasted: {org:String},
    {start:DateTime}, {end:DateTime}; tables resolve in the client's database."""
    text = template.replace("{db}.", "")
    for quoted, bound in (("'{{org_id}}'", "{org:String}"), ("'{org_id}'", "{org:String}"),
                          ("toDateTime('{{start}}')", "{start:DateTime}"),
                          ("toDateTime('{start}')", "{start:DateTime}"),
                          ("toDateTime('{{end}}')", "{end:DateTime}"),
                          ("toDateTime('{end}')", "{end:DateTime}")):  # fmt: skip
        text = text.replace(quoted, bound)
    if "{{" in text or "'{" in text:
        raise ValueError("A ClickHouse template kept a pasted placeholder.")
    return text
