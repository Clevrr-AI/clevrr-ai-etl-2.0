"""Meta Ads pipelines (plan 7.3), as legacy runs them (`run-all-for-connector meta_ads`).

`meta-ads`: insights + creatives + structure per ad account -> advertising,
ad_product_mapping and campaign_structure -> ClickHouse -> check.
`meta-ads-region`: spend by region (state) -> advertising_geo_daily -> ClickHouse -> check;
its own watermark, as legacy's `ads_region`.

The run's days are IST dates. An incremental run covers [window start, the day after the
window end): it includes today, so an intraday tick (`etl tick meta_ads`) refreshes today's
partial figures, and the 3-day look-back re-pulls the days Meta is still attributing. A
backfill's --to stays exclusive. (Legacy ran from the watermark to today 00:00 IST, never
fetched today and had no look-back.)
"""

from __future__ import annotations

from collections.abc import Generator, Mapping
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, LiteralString
from zoneinfo import ZoneInfo

import httpx
from psycopg import sql

from etl.checks.compare import close_figures
from etl.core import ch
from etl.core.ch_writer import ChWriter, Slice
from etl.core.model import Mode, Org, Pipeline, RunContext, Step, StepFailed, StepResult, Window
from etl.core.pg_load import run_sql_file
from etl.sources.meta import sql_meta
from etl.sources.meta.creatives import page_tokens
from etl.sources.meta.fetch import accounts, load_account, load_region
from etl.sources.meta.graph import Graph

SQL_DIR = Path(__file__).resolve().parent / "sql"
IST = ZoneInfo("Asia/Kolkata")
DEFAULT_API_VERSION = "v24.0"  # legacy META_ADS_API_VERSION default
TRANSFORMS = ("meta_advertising.sql", "meta_ad_product_mapping.sql", "meta_campaign_structure.sql")
SOURCE = Slice(source_column="source_type", source=sql_meta.SOURCE)


def days(mode: Mode, window: Window) -> tuple[date, date]:
    """IST days [first, end). Incremental: the day the window ends in (today) is included;
    backfill: the end day is exclusive, as --to says."""
    first, last = window.start.astimezone(IST).date(), window.end.astimezone(IST).date()
    return first, (last + timedelta(days=1) if mode is Mode.INCREMENTAL else last)


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time(), UTC)


def _wall(moment: datetime) -> datetime:
    """The IST wall clock, naive: how legacy's naive timestamps sit in ClickHouse."""
    return moment.astimezone(IST).replace(tzinfo=None)


def _utc_naive(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)


def _rows(ctx: RunContext, query: LiteralString | sql.Composed,
          params: Mapping[str, Any]) -> list[tuple[Any, ...]]:  # fmt: skip
    with ctx.pg.connection() as conn:
        return conn.execute(query, dict(params)).fetchall()


@contextmanager
def _graph(ctx: RunContext, org: Org) -> Generator[Graph]:
    token = str(org.credentials.get("access_token") or "")
    if not token:
        raise StepFailed("The Meta connector has no access_token.")
    found = _rows(ctx, sql_meta.API_VERSION, {})
    version = (found[0][0] if found else None) or DEFAULT_API_VERSION
    with httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0)) as http:
        yield Graph(f"https://graph.facebook.com/{version}", token, http)


def extract_meta(ctx: RunContext, org: Org, window: Window) -> StepResult:
    span = days(ctx.mode, window)
    if span[0] >= span[1]:
        return StepResult(detail={"days": 0})
    detail: dict[str, Any] = {"days": [d.isoformat() for d in span]}
    fetched = 0
    with _graph(ctx, org) as graph, ctx.pg.connection() as conn:
        tokens = page_tokens(graph)
        for account in accounts(org.credentials):
            result = load_account(conn, graph, org.org_id, ctx.job_id, account, span,
                                  ctx.started_at.date(), tokens)  # fmt: skip
            fetched += int(result["insights"])
            detail[account] = result
    return StepResult(fetched, fetched, detail=detail)


def _params(ctx: RunContext, org: Org, window: Window) -> dict[str, Any]:
    first, end = days(ctx.mode, window)
    return {"org": org.org_id, "start": _midnight(first), "end": _midnight(end),
            "first_day": first, "end_day": end}  # fmt: skip


def transform_meta(ctx: RunContext, org: Org, window: Window) -> StepResult:
    params = _params(ctx, org, window)
    detail: dict[str, list[int]] = {}
    with ctx.pg.connection() as conn, conn.transaction():
        for name in TRANSFORMS:
            detail[name.removesuffix(".sql")] = run_sql_file(conn, SQL_DIR / name, params)
    return StepResult(detail=detail)


def sync_meta(ctx: RunContext, org: Org, window: Window) -> StepResult:
    """Legacy: delete the org's Meta rows (advertising: of the run's days) in ClickHouse,
    then insert; when Postgres has none, keep what ClickHouse has."""
    params = _params(ctx, org, window)
    writer = ChWriter(ctx.ch, ctx.settings.clickhouse)
    detail: dict[str, int] = {}
    columns = writer.columns("advertising")
    rows = [list(r) for r in _rows(ctx, sql_meta.advertising_rows(columns), params)]
    if rows:
        days_slice = Slice(time_column="created_at", start=_wall(params["start"]),
                           end=_wall(params["end"]), source_column="source_type",
                           source=sql_meta.SOURCE)  # fmt: skip
        detail["advertising"] = writer.replace("advertising", org.org_id, days_slice, columns, rows)
    for table in ("ad_product_mapping", "campaign_structure"):
        columns = writer.columns(table)
        rows = [list(r) for r in _rows(ctx, sql_meta.org_rows(table, columns), params)]
        if rows:
            detail[table] = writer.replace(table, org.org_id, SOURCE, columns, rows)
    total = sum(detail.values())
    return StepResult(total, total, detail=detail)


def check_meta(ctx: RunContext, org: Org, window: Window) -> StepResult:
    params = _params(ctx, org, window)
    labels = ("rows", "spend", "impressions")
    pg = _rows(ctx, sql_meta.PG_ADVERTISING, params)[0]
    close_figures("advertising (raw vs Postgres)", labels,
                  _rows(ctx, sql_meta.RAW_INSIGHTS, params)[0], pg)  # fmt: skip
    ch_params = {"org": org.org_id, "start": _wall(params["start"]), "end": _wall(params["end"])}
    detail: dict[str, Any] = {
        "advertising": close_figures("advertising", labels, pg,
                                     ch.rows(ctx.ch, sql_meta.CH_ADVERTISING, ch_params)[0]),
    }  # fmt: skip
    for table, final in (("ad_product_mapping", False), ("campaign_structure", True)):
        pg_n = _rows(ctx, sql_meta.pg_count(table), params)[0]
        ch_n = ch.rows(ctx.ch, sql_meta.ch_count(table, final), {"org": org.org_id})[0]
        detail[table] = close_figures(table, ("rows",), pg_n, ch_n)
    return StepResult(detail=detail)


def extract_region(ctx: RunContext, org: Org, window: Window) -> StepResult:
    span = days(ctx.mode, window)
    if span[0] >= span[1]:
        return StepResult(detail={"days": 0})
    detail: dict[str, Any] = {"days": [d.isoformat() for d in span]}
    with _graph(ctx, org) as graph, ctx.pg.connection() as conn:
        for account in accounts(org.credentials):
            detail[account] = load_region(conn, graph, org.org_id, ctx.job_id, account, span,
                                          ctx.started_at.date())  # fmt: skip
    fetched = sum(v for v in detail.values() if isinstance(v, int))
    return StepResult(fetched, fetched, detail=detail)


def transform_geo(ctx: RunContext, org: Org, window: Window) -> StepResult:
    with ctx.pg.connection() as conn, conn.transaction():
        counts = run_sql_file(conn, SQL_DIR / "meta_ad_geo.sql", {"org": org.org_id})
    return StepResult(detail={"meta_ad_geo": counts})


def _geo_span(ctx: RunContext, org: Org) -> tuple[datetime, datetime] | None:
    lo, hi = _rows(ctx, sql_meta.GEO_SPAN, {"org": org.org_id})[0]
    return None if lo is None else (lo, hi)


def sync_geo(ctx: RunContext, org: Org, window: Window) -> StepResult:
    """Legacy: the created_at span the transform touched (last_synced, 7 days), every row in
    it; delete that span in ClickHouse, then insert."""
    span = _geo_span(ctx, org)
    if span is None:
        return StepResult(detail={"geo": 0})
    writer = ChWriter(ctx.ch, ctx.settings.clickhouse)
    columns = writer.columns("advertising_geo_daily")
    params = {"org": org.org_id, "lo": span[0], "hi": span[1]}
    rows = [list(r) for r in _rows(ctx, sql_meta.geo_rows(columns), params)]
    geo_slice = Slice(time_column="created_at", start=_utc_naive(span[0]),
                      end=_utc_naive(span[1]) + timedelta(seconds=1),
                      source_column="source_type", source=sql_meta.SOURCE)  # fmt: skip
    written = writer.replace("advertising_geo_daily", org.org_id, geo_slice, columns, rows)
    return StepResult(len(rows), written)


def check_geo(ctx: RunContext, org: Org, window: Window) -> StepResult:
    span = _geo_span(ctx, org)
    if span is None:
        return StepResult(detail={"geo": 0})
    pg = _rows(ctx, sql_meta.PG_GEO, {"org": org.org_id, "lo": span[0], "hi": span[1]})[0]
    chv = ch.rows(ctx.ch, sql_meta.CH_GEO, {"org": org.org_id, "lo": _utc_naive(span[0]),
                                            "hi": _utc_naive(span[1])})[0]  # fmt: skip
    return StepResult(detail={"geo": close_figures("advertising_geo_daily", ("rows", "spend"),
                                                   pg, chv)})  # fmt: skip


META_ADS = Pipeline(
    name="meta-ads",
    source="meta_ads",
    steps=(
        Step("extract_meta", extract_meta),
        Step("transform_meta", transform_meta),
        Step("sync_meta", sync_meta),
        Step("check_meta", check_meta),
    ),
    lookback=timedelta(days=3),  # Meta keeps attributing for days; re-pull them (legacy: none)
    first_window=timedelta(days=365),  # legacy compute_window(ads, 365)
    timeout=timedelta(hours=2),
    schedule="0 * * * *",  # etl tick meta_ads, hourly; each org on its etl_schedules interval
)
META_ADS_REGION = Pipeline(
    name="meta-ads-region",
    source="meta_ads",
    steps=(
        Step("extract_region", extract_region),
        Step("transform_geo", transform_geo),
        Step("sync_geo", sync_geo),
        Step("check_geo", check_geo),
    ),
    lookback=timedelta(days=3),
    first_window=timedelta(days=365),
    timeout=timedelta(hours=1),
    schedule="0 * * * *",
)
