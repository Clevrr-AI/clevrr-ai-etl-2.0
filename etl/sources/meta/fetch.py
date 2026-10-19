"""Per ad account, in legacy's order (fetch_runner.py, meta_ads branch): insights (a failure
fails the run), then creatives for the ads seen in insights, then the campaign/ad set/ad
structure. Creative and structure failures are logged and recorded in the step detail and the
run goes on, as legacy did; the region breakdown is its own pipeline (own watermark, legacy's
`ads_region`)."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import date, datetime
from typing import Any

from psycopg import Connection

from etl.core.logging import get_logger
from etl.sources.meta import raw
from etl.sources.meta.creatives import fetch_creatives
from etl.sources.meta.graph import Graph, GraphError, insights

log = get_logger(__name__)
CAMPAIGN_STATUS_FILTER = ["ACTIVE", "PAUSED", "IN_PROCESS", "WITH_ISSUES", "ARCHIVED"]
CAMPAIGN_FIELDS = ("id,name,account_id,status,effective_status,objective,daily_budget,"
                   "lifetime_budget,bid_strategy,start_time,stop_time,updated_time")  # fmt: skip
ADSET_FIELDS = ("id,name,account_id,campaign_id,status,effective_status,optimization_goal,"
                "billing_event,bid_strategy,daily_budget,lifetime_budget,start_time,end_time,"
                "updated_time,learning_stage_info")  # fmt: skip
AD_FIELDS = (
    "id,name,account_id,adset_id,campaign_id,status,effective_status,creative{id},updated_time"
)
Row = Callable[[str, str, Mapping[str, Any], datetime], list[Any]]


def accounts(credentials: Mapping[str, Any]) -> list[str]:
    ids = credentials.get("ad_account_ids") or [
        credentials.get("ad_account_id") or credentials.get("account_id")
    ]
    return [i if str(i).startswith("act_") else f"act_{i}" for i in map(str, ids) if i]


def _edge(graph: Graph, account: str, edge: str, fields: str,
          status_filter: Sequence[str] | None) -> list[dict[str, Any]]:  # fmt: skip
    """All objects of an edge; a status filter Meta refuses on the first page is dropped
    (legacy _paged_fetch)."""
    base: dict[str, Any] = {"fields": fields, "limit": 200}

    def run(params: dict[str, Any]) -> Iterator[list[dict[str, Any]]]:
        return graph.paged(f"{account}/{edge}", params, stop_on_empty=True)

    if status_filter:
        rule = [{"field": "effective_status", "operator": "IN", "value": list(status_filter)}]
        filtered = {**base, "filtering": json.dumps(rule)}
        pages = run(filtered)
        try:
            first = next(pages)
        except GraphError:
            return [o for page in run(base) for o in page]
        return [*first, *(o for page in pages for o in page)]
    return [o for page in run(base) for o in page]


def _rows(org: str, job: str, records: Sequence[Mapping[str, Any]], to_row: Row,
          account: str, now: datetime) -> list[list[Any]]:  # fmt: skip
    plain = account.removeprefix("act_")
    return [to_row(org, job, {**r, "account_id": r.get("account_id") or plain}, now)
            for r in records if r.get("id")]  # fmt: skip


def load_structure(conn: Connection[Any], graph: Graph, org: str, job: str,
                   account: str) -> dict[str, int]:  # fmt: skip
    now = raw.now_utc()
    campaigns = _edge(graph, account, "campaigns", CAMPAIGN_FIELDS, CAMPAIGN_STATUS_FILTER)
    adsets = _edge(graph, account, "adsets", ADSET_FIELDS, None)
    ads = _edge(graph, account, "ads", AD_FIELDS, None)
    with conn.transaction():
        return {
            "campaigns": raw.load(conn, "raw_meta_campaigns", raw.CAMPAIGN_COLUMNS,
                                  ("organization_id", "campaign_id"),
                                  _rows(org, job, campaigns, raw.campaign_row, account, now)),
            "adsets": raw.load(conn, "raw_meta_ad_sets", raw.ADSET_COLUMNS,
                               ("organization_id", "adset_id"),
                               _rows(org, job, adsets, raw.adset_row, account, now)),
            "ads": raw.load(conn, "raw_meta_ads", raw.AD_COLUMNS, ("organization_id", "ad_id"),
                            _rows(org, job, ads, raw.ad_row, account, now)),
        }  # fmt: skip


def load_account(conn: Connection[Any], graph: Graph, org: str, job: str, account: str,
                 days: tuple[date, date], today: date,
                 page_tokens: dict[str, str]) -> dict[str, Any]:  # fmt: skip
    rows = [r for page in insights(graph, account, *days, fields=raw.INSIGHT_FIELDS,
                                   level="ad", today=today,
                                   extra={"use_account_attribution_setting": "true"})
            for r in page]  # fmt: skip
    with conn.transaction():
        loaded = raw.load(conn, "raw_meta_ad_insights", raw.INSIGHT_COLUMNS,
                          ("organization_id", "ad_id", "date_start"),
                          [raw.insight_row(org, job, r) for r in rows])  # fmt: skip
    detail: dict[str, Any] = {"insights": loaded}
    ad_ids = {str(r["ad_id"]) for r in rows if r.get("ad_id")}
    try:
        if ad_ids:
            records = fetch_creatives(graph, account, ad_ids, page_tokens)
            now = raw.now_utc()
            with conn.transaction():
                rows_c = [raw.creative_row(org, job, r, now) for r in records]
                detail["creatives"] = raw.load(
                    conn,
                    "raw_meta_ad_creative",
                    raw.CREATIVE_COLUMNS,
                    ("organization_id", "ad_id"),
                    rows_c,
                    raw.CREATIVE_KEEP,
                )
    except Exception as exc:  # noqa: BLE001 - legacy: a creative failure never fails the run
        detail["creatives_failed"] = type(exc).__name__
        log.warning("meta.creatives_failed", account=account, error_type=type(exc).__name__)
    try:
        detail["structure"] = load_structure(conn, graph, org, job, account)
    except Exception as exc:  # noqa: BLE001 - legacy: a structure failure never fails the run
        detail["structure_failed"] = type(exc).__name__
        log.warning("meta.structure_failed", account=account, error_type=type(exc).__name__)
    return detail


def load_region(conn: Connection[Any], graph: Graph, org: str, job: str, account: str,
                days: tuple[date, date], today: date) -> int:  # fmt: skip
    now = raw.now_utc()
    by_key: dict[tuple[Any, ...], list[Any]] = {}
    for page in insights(graph, account, *days, fields=raw.REGION_FIELDS, level="campaign",
                         today=today, extra={"breakdowns": "region"}):  # fmt: skip
        for r in page:
            row = raw.region_row(org, job, r, now)
            by_key[(row[6], row[2], row[4])] = row  # (campaign_id, date_start, region), last wins
    with conn.transaction():
        return raw.load(conn, "raw_meta_ad_insights_by_region", raw.REGION_COLUMNS,
                        ("organization_id", "campaign_id", "date_start", "region"),
                        list(by_key.values()))  # fmt: skip
