"""Per Google Ads customer, in legacy's order (run_google_ads_fetcher + load_google_ads_raw):
the dated datasets in 30-day chunks (7 for product performance), the click map for the last 3
days, the conversion-action dictionary, then the ad creative snapshot.

Legacy's soft failures are kept: product performance (a 400 is the routine "no Shopping
campaigns" case), the click map and the creative snapshot are logged and recorded in the step
detail. Everything else fails the run.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta
from typing import Any

from psycopg import Connection

from etl.core.logging import get_logger
from etl.core.pg_load import upsert
from etl.sources.google_ads import datasets as ds
from etl.sources.google_ads.api import GoogleAds, customer_id

log = get_logger(__name__)
CLICK_VIEW_DAYS = 3


def chunks(start: date, end: date, days: int) -> Iterator[tuple[date, date]]:
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=days), end)
        yield cur, nxt
        cur = nxt


def customers(api: GoogleAds, credentials: dict[str, Any]) -> tuple[list[str], str | None]:
    """The leaf accounts to read and the login-customer-id (legacy manager handling)."""
    cid = customer_id(credentials.get("customer_id") or credentials.get("client_customer_id"))
    if not cid:
        raise ValueError("The Google Ads connector has no customer_id; reconnect and pick one.")
    login = customer_id(credentials.get("login_customer_id"))
    if api.is_manager(cid, login) is True:
        leaves = api.leaf_accounts(cid, login or cid)
        if not leaves:
            raise ValueError("A manager account with no accessible client accounts.")
        return leaves, login or cid
    return [cid], login


def _dataset(conn: Connection[Any], api: GoogleAds, d: ds.Dataset, org: str, job: str,
             cid: str, login: str | None, span: tuple[date, date]) -> int:  # fmt: skip
    written = 0
    now = datetime.now().astimezone()
    for start, end in chunks(*span, d.chunk_days):
        rows = api.search(cid, d.query.format(start=start.isoformat(), end=end.isoformat()), login)
        built = [ds.raw_row(d, org, job, d.normalize(r), now) for r in rows]
        with conn.transaction():
            written += upsert(conn, d.table, d.columns, d.key, built, conflict=d.conflict)
    return written


def click_days(today: date) -> list[str]:
    return [(today - timedelta(days=back)).isoformat() for back in range(1, CLICK_VIEW_DAYS + 1)]


def load_customer(conn: Connection[Any], api: GoogleAds, org: str, job: str, cid: str,
                  login: str | None, span: tuple[date, date], today: date,
                  clicks: list[list[Any]]) -> dict[str, Any]:  # fmt: skip
    login_for = login if login and login != cid else None
    detail: dict[str, Any] = {}
    for d in ds.DATED:
        try:
            detail[d.name] = _dataset(conn, api, d, org, job, cid, login_for, span)
        except Exception as exc:
            if not d.optional:
                raise
            detail[f"{d.name}_failed"] = getattr(exc, "status", None) or type(exc).__name__
            log.warning("google_ads.dataset_failed", dataset=d.name, error_type=type(exc).__name__)
    for day in click_days(today):
        try:
            rows = api.search(cid, ds.CLICK_VIEW.format(day=day), login_for)
            clicks.extend(r for r in (ds.click_row(org, day, x) for x in rows) if r)
        except Exception as exc:  # noqa: BLE001 - legacy: click_view is best-effort
            detail.setdefault("click_view_failed", []).append(day)
            log.warning("google_ads.click_view_failed", day=day, error_type=type(exc).__name__)
    actions = [
        ds.conversion_action_row(r) for r in api.search(cid, ds.CONVERSION_ACTIONS, login_for)
    ]
    with conn.transaction():
        detail["conversion_actions"] = upsert(
            conn,
            "raw_google_ads_conversion_action",
            ("resource_name", "name", "category", "status"),
            ("resource_name",),
            actions,
        )
    try:
        assets: ds.Assets = dict(
            e for e in (ds.asset_entry(r) for r in api.search(cid, ds.ASSETS, login_for)) if e
        )
        creatives = [ds.creative_row(org, job, r, assets)
                     for r in api.search(cid, ds.AD_CREATIVES, login_for)]  # fmt: skip
        with conn.transaction():
            detail["ad_creatives"] = upsert(conn, "raw_google_ads_ad_creative", ds.CREATIVE_COLUMNS,
                                            ("organization_id", "ad_id"), creatives)  # fmt: skip
    except Exception as exc:  # noqa: BLE001 - legacy: the creative snapshot is best-effort
        detail["ad_creatives_failed"] = type(exc).__name__
        log.warning("google_ads.creatives_failed", error_type=type(exc).__name__)
    return detail
