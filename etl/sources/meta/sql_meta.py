"""SQL for the Meta pipelines' sync and check steps (a builder file: identifiers come from
fixed constants and ClickHouse's own column list; values are bound)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from psycopg import sql

SOURCE = "meta_ads"
API_VERSION: Final = "SELECT api_version FROM public.connectors WHERE key = 'meta_ads'"
# advertising rows of the run's days (Postgres created_at is the IST wall clock of the day's
# UTC midnight; the session time zone makes the comparison with %(start)s/%(end)s exact).
_ADVERTISING_WHERE: Final = (
    " WHERE organization_id = %(org)s AND source_type = 'meta_ads'"
    " AND created_at >= %(start)s AND created_at < %(end)s"
)
_ORG_SOURCE: Final = " WHERE organization_id = %(org)s AND source_type = 'meta_ads'"
GEO_SPAN: Final = (
    "SELECT min(created_at), max(created_at) FROM advertising_geo_daily"
    " WHERE organization_id = %(org)s AND source_type = 'meta_ads'"
    " AND last_synced >= now() - interval '7 days'"
)
_GEO_WHERE: Final = (
    " WHERE organization_id = %(org)s AND source_type = 'meta_ads'"
    " AND created_at >= %(lo)s AND created_at <= %(hi)s"
)
RAW_INSIGHTS: Final = (
    "SELECT count(*), sum(spend), sum(impressions) FROM raw_meta_ad_insights"
    " WHERE organization_id = %(org)s AND date_start >= %(first_day)s AND date_start < %(end_day)s"
)
PG_ADVERTISING: Final = (
    "SELECT count(*), sum(spend), sum(impressions) FROM advertising" + _ADVERTISING_WHERE
)
CH_ADVERTISING: Final = (
    "SELECT count(), sum(spend), sum(impressions) FROM advertising"
    " WHERE organization_id = {org:String} AND source_type = 'meta_ads'"
    " AND created_at >= {start:DateTime64(3)} AND created_at < {end:DateTime64(3)}"
)
PG_GEO: Final = "SELECT count(*), sum(spend) FROM advertising_geo_daily" + _GEO_WHERE
CH_GEO: Final = (
    "SELECT count(), sum(spend) FROM advertising_geo_daily"
    " WHERE organization_id = {org:String} AND source_type = 'meta_ads'"
    " AND created_at >= {lo:DateTime64(3)} AND created_at <= {hi:DateTime64(3)}"
)


def _select(table: str, columns: Sequence[str], where: str) -> sql.Composed:
    return sql.SQL("SELECT {c} FROM {t}" + where).format(  # pyright: ignore[reportArgumentType]
        c=sql.SQL(", ").join(sql.Identifier(x) for x in columns), t=sql.Identifier(table)
    )


def advertising_rows(columns: Sequence[str]) -> sql.Composed:
    return _select("advertising", columns, _ADVERTISING_WHERE)


def org_rows(table: str, columns: Sequence[str]) -> sql.Composed:
    """All the org's Meta rows of a full-refresh table (ad_product_mapping, campaign_structure)."""
    return _select(table, columns, _ORG_SOURCE)


def geo_rows(columns: Sequence[str]) -> sql.Composed:
    return _select("advertising_geo_daily", columns, _GEO_WHERE)


def pg_count(table: str) -> sql.Composed:
    return sql.SQL("SELECT count(*) FROM {t}" + _ORG_SOURCE).format(t=sql.Identifier(table))


def ch_count(table: str, final: bool) -> str:
    return ("SELECT count() FROM " + table + (" FINAL" if final else "")
            + " WHERE organization_id = {org:String} AND source_type = 'meta_ads'")  # fmt: skip
