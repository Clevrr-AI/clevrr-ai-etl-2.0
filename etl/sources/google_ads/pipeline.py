"""Google Ads pipeline (plan 7.3), as legacy runs it (`run-all-for-connector google_ads`):
the datasets per customer -> advertising, ad_product_mapping, google_ads_product_performance
-> ClickHouse -> check. The click map goes straight to ClickHouse `google_click_map`.

The run's days are IST dates, like Meta: an incremental run includes today (an intraday
`etl tick google_ads` refreshes today's partial figures) and re-pulls a 3-day look-back for
late conversions; a backfill's --to stays exclusive. (Legacy: from the watermark to today
00:00 IST, no look-back; first run 3650 days, here a backfill run instead.)
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, LiteralString
from zoneinfo import ZoneInfo

import httpx
from psycopg import sql

from etl.checks.compare import close_figures
from etl.core import ch
from etl.core.ch_writer import ChWriter, Slice
from etl.core.logging import get_logger
from etl.core.model import Mode, Org, Pipeline, RunContext, Step, StepFailed, StepResult, Window
from etl.core.pg_load import run_sql_file
from etl.sources.google_ads import sql_google
from etl.sources.google_ads.api import GoogleAds, refresh_token
from etl.sources.google_ads.datasets import CLICK_COLUMNS
from etl.sources.google_ads.fetch import customers, load_customer

log = get_logger(__name__)
SQL_DIR = Path(__file__).resolve().parent / "sql"
IST = ZoneInfo("Asia/Kolkata")
TRANSFORMS = ("google_advertising.sql", "google_ad_product_mapping.sql",
              "google_product_performance.sql")  # fmt: skip


def days(mode: Mode, window: Window) -> tuple[date, date]:
    """IST days [first, end). Incremental: the day the window ends in (today) is included;
    backfill: the end day is exclusive, as --to says."""
    first, last = window.start.astimezone(IST).date(), window.end.astimezone(IST).date()
    return first, (last + timedelta(days=1) if mode is Mode.INCREMENTAL else last)


def _params(ctx: RunContext, org: Org, window: Window) -> dict[str, Any]:
    first, end = days(ctx.mode, window)
    return {"org": org.org_id, "start": datetime.combine(first, time(), UTC),
            "end": datetime.combine(end, time(), UTC)}  # fmt: skip


def _wall(moment: datetime) -> datetime:
    return moment.astimezone(IST).replace(tzinfo=None)


def _utc_naive(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)


def _rows(ctx: RunContext, query: LiteralString | sql.Composed,
          params: Mapping[str, Any]) -> list[tuple[Any, ...]]:  # fmt: skip
    with ctx.pg.connection() as conn:
        return conn.execute(query, dict(params)).fetchall()


def _client(ctx: RunContext, org: Org, http: httpx.Client) -> GoogleAds:
    cfg = ctx.settings.google_ads
    if cfg.client_id is None or cfg.client_secret is None:
        raise StepFailed("GOOGLE_ADS__CLIENT_ID / GOOGLE_ADS__CLIENT_SECRET are not set.")
    creds = dict(org.credentials)
    token = refresh_token(http, creds, cfg.client_id.get_secret_value(),
                          cfg.client_secret.get_secret_value())  # fmt: skip
    developer = str(creds.get("developer_token") or "") or (
        cfg.developer_token.get_secret_value() if cfg.developer_token else "")  # fmt: skip
    if not developer:
        raise StepFailed("The Google Ads connector has no developer_token.")
    return GoogleAds(http, token, developer)


def extract_google(ctx: RunContext, org: Org, window: Window) -> StepResult:
    span = days(ctx.mode, window)
    if span[0] >= span[1]:
        return StepResult(detail={"days": 0})
    detail: dict[str, Any] = {"days": [d.isoformat() for d in span]}
    clicks: list[list[Any]] = []
    today = ctx.started_at.astimezone(IST).date()
    with httpx.Client(timeout=httpx.Timeout(120.0, connect=30.0)) as http:
        api = _client(ctx, org, http)
        try:
            ids, login = customers(api, dict(org.credentials))
        except ValueError as exc:
            raise StepFailed(str(exc)) from None
        fetched = 0
        with ctx.pg.connection() as conn:
            for cid in ids:
                result = load_customer(conn, api, org.org_id, ctx.job_id, cid, login, span,
                                       today, clicks)  # fmt: skip
                fetched += int(result.get("ad_performance", 0))
                detail[cid] = result
    try:  # legacy: the click map is best-effort
        latest = list({r[1]: r for r in clicks}.values())  # one row per gclid, as legacy
        detail["click_map"] = ChWriter(ctx.ch, ctx.settings.clickhouse).append(
            "google_click_map", CLICK_COLUMNS, latest
        )
    except Exception as exc:  # noqa: BLE001
        detail["click_map_failed"] = type(exc).__name__
        log.warning("google_ads.click_map_failed", error_type=type(exc).__name__)
    return StepResult(fetched, fetched, detail=detail)


def transform_google(ctx: RunContext, org: Org, window: Window) -> StepResult:
    params = _params(ctx, org, window)
    detail: dict[str, list[int]] = {}
    with ctx.pg.connection() as conn, conn.transaction():
        for name in TRANSFORMS:
            detail[name.removesuffix(".sql")] = run_sql_file(conn, SQL_DIR / name, params)
    return StepResult(detail=detail)


def sync_google(ctx: RunContext, org: Org, window: Window) -> StepResult:
    """Legacy: delete the org's rows (advertising and product performance: of the run's days)
    in ClickHouse, then insert; when Postgres has none, keep what ClickHouse has."""
    params = _params(ctx, org, window)
    writer = ChWriter(ctx.ch, ctx.settings.clickhouse)
    detail: dict[str, int] = {}
    plan = (
        ("advertising", sql_google.advertising_rows,
         Slice(time_column="created_at", start=_wall(params["start"]), end=_wall(params["end"]),
               source_column="source_type", source=sql_google.SOURCE)),
        ("ad_product_mapping", sql_google.mapping_rows,
         Slice(source_column="source_type", source=sql_google.SOURCE)),
        ("google_ads_product_performance", sql_google.product_rows,
         Slice(time_column="report_date", start=_utc_naive(params["start"]),
               end=_utc_naive(params["end"]))),
    )  # fmt: skip
    for table, select, slice_ in plan:
        columns = writer.columns(table)
        rows = [list(r) for r in _rows(ctx, select(columns), params)]
        if rows:
            detail[table] = writer.replace(table, org.org_id, slice_, columns, rows)
    total = sum(detail.values())
    return StepResult(total, total, detail=detail)


def check_google(ctx: RunContext, org: Org, window: Window) -> StepResult:
    params = _params(ctx, org, window)
    wall = {"org": org.org_id, "start": _wall(params["start"]), "end": _wall(params["end"])}
    utc = {"org": org.org_id, "start": _utc_naive(params["start"]),
           "end": _utc_naive(params["end"])}  # fmt: skip
    detail = {
        "advertising": close_figures(
            "advertising", ("rows", "spend", "impressions"),
            _rows(ctx, sql_google.PG_ADVERTISING, params)[0],
            ch.rows(ctx.ch, sql_google.CH_ADVERTISING, wall)[0]),
        "google_ads_product_performance": close_figures(
            "google_ads_product_performance", ("rows", "spend"),
            _rows(ctx, sql_google.PG_PRODUCT, params)[0],
            ch.rows(ctx.ch, sql_google.CH_PRODUCT, utc)[0]),
        "ad_product_mapping": close_figures(
            "ad_product_mapping", ("rows",), _rows(ctx, sql_google.PG_MAPPING, params)[0],
            ch.rows(ctx.ch, sql_google.CH_MAPPING, {"org": org.org_id})[0]),
    }  # fmt: skip
    return StepResult(detail=detail)


GOOGLE_ADS = Pipeline(
    name="google-ads",
    source="google_ads",
    steps=(
        Step("extract_google", extract_google),
        Step("transform_google", transform_google),
        Step("sync_google", sync_google),
        Step("check_google", check_google),
    ),
    lookback=timedelta(days=3),  # conversions land up to 15 h late; re-pull (legacy: none)
    first_window=timedelta(days=30),  # legacy took 3650 days; history comes from a backfill run
    timeout=timedelta(hours=2),
    schedule="0 * * * *",  # etl tick google_ads, hourly; each org on its etl_schedules interval
)
