"""SQL for the Google Ads pipeline's sync and check steps (a builder file: identifiers come
from fixed constants and ClickHouse's own column list; values are bound)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from psycopg import sql

SOURCE = "google_ads"
# advertising.created_at is the IST wall clock of the day's UTC midnight (session time zone).
_DAYS: Final = (
    " WHERE organization_id = %(org)s AND source_type = 'google_ads'"
    " AND created_at >= %(start)s AND created_at < %(end)s"
)
_PRODUCT_DAYS: Final = (
    " WHERE organization_id = %(org)s AND report_date >= %(start)s AND report_date < %(end)s"
)
_ORG_SOURCE: Final = " WHERE organization_id = %(org)s AND source_type = 'google_ads'"
PG_ADVERTISING: Final = "SELECT count(*), sum(spend), sum(impressions) FROM advertising" + _DAYS
CH_ADVERTISING: Final = (
    "SELECT count(), sum(spend), sum(impressions) FROM advertising"
    " WHERE organization_id = {org:String} AND source_type = 'google_ads'"
    " AND created_at >= {start:DateTime64(3)} AND created_at < {end:DateTime64(3)}"
)
PG_PRODUCT: Final = (
    "SELECT count(*), sum(spend) FROM google_ads_product_performance" + _PRODUCT_DAYS
)
CH_PRODUCT: Final = (
    "SELECT count(), sum(spend) FROM google_ads_product_performance FINAL"
    " WHERE organization_id = {org:String}"
    " AND report_date >= {start:DateTime64(3)} AND report_date < {end:DateTime64(3)}"
)
PG_MAPPING: Final = "SELECT count(*) FROM ad_product_mapping" + _ORG_SOURCE
CH_MAPPING: Final = (
    "SELECT count() FROM ad_product_mapping"
    " WHERE organization_id = {org:String} AND source_type = 'google_ads'"
)


def _select(table: str, columns: Sequence[str], where: str) -> sql.Composed:
    return sql.SQL("SELECT {c} FROM {t}" + where).format(  # pyright: ignore[reportArgumentType]
        c=sql.SQL(", ").join(sql.Identifier(x) for x in columns), t=sql.Identifier(table)
    )


def advertising_rows(columns: Sequence[str]) -> sql.Composed:
    return _select("advertising", columns, _DAYS)


def mapping_rows(columns: Sequence[str]) -> sql.Composed:
    return _select("ad_product_mapping", columns, _ORG_SOURCE)


def product_rows(columns: Sequence[str]) -> sql.Composed:
    return _select("google_ads_product_performance", columns, _PRODUCT_DAYS)
