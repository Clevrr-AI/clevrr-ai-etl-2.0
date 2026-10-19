"""The data reliability report (plan 7.4), ported from legacy app/scripts/
data_reliability_report.py: for one org and one IST day, the primary entity's count at each
stage (Shopify API, raw, Postgres, ClickHouse) and each metric from the raw tables, Postgres
and ClickHouse, with legacy's verdict (PASS when every stage agrees within 0.1%).

Like legacy it reports and never fails a run (the pipelines' own check steps do that; legacy's
returns formulas also give known false FAILs). Each org's results are recorded in the step
detail; an org with a FAIL or ERR verdict is posted to Slack. Legacy's dashboard stage (the
app's metric engine) is not ported here: it belongs to the backend.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from itertools import pairwise
from typing import Any
from zoneinfo import ZoneInfo

from etl.checks import sql_reliability as defs
from etl.core import ch
from etl.core.alerts import post_message
from etl.core.model import Org, Pipeline, RunContext, Step, StepResult, Window
from etl.sources.shopify.shop import connect

IST = ZoneInfo("Asia/Kolkata")
TOLERANCE_PCT = 0.1  # legacy RELIABILITY_TOLERANCE_PCT
CONNECTOR = {"shopify": "shopify", "meta_ads": "meta_ads", "google_ads": "google_ads"}


def report_days(window: Window) -> list[date]:
    """The IST days to report: [window start, window end), at least the day before the end."""
    first = window.start.astimezone(IST).date()
    end = window.end.astimezone(IST).date()
    first = min(first, end - timedelta(days=1))
    return [first + timedelta(days=i) for i in range((end - first).days)]


def _pg(ctx: RunContext, query: str, params: Mapping[str, Any]) -> float | None:
    with ctx.pg.connection() as conn:
        row = conn.execute(query.encode(), dict(params)).fetchone()
    return None if row is None or row[0] is None else float(row[0])


def _ch(ctx: RunContext, template: str, params: Mapping[str, Any]) -> float | None:
    rows = ch.rows(ctx.ch, defs.ch_query(template), params)
    return None if not rows or rows[0][0] is None else float(rows[0][0])


def _try(fn: Any, *args: Any) -> tuple[float | None, str | None]:
    try:
        return fn(*args), None
    except Exception as exc:  # noqa: BLE001 - legacy records the error and goes on
        return None, type(exc).__name__


def verdict(values: list[float | None], errors: list[str | None]) -> str:
    """Legacy compute_verdict: ERR on any error, EMPTY when all zero, PASS when each stage
    matches the next within the tolerance, FAIL otherwise, WARN when nothing compared."""
    if any(errors):
        return "ERR"
    present = [v for v in values if v is not None]
    if not present or all(v == 0 for v in present):
        return "EMPTY"
    checks: list[bool] = []
    for a, b in pairwise(values):
        if a is None or b is None:
            continue
        if a == 0 and b == 0:
            checks.append(True)
        else:
            checks.append(a != 0 and abs(a - b) / abs(a) * 100 <= TOLERANCE_PCT)
    if not checks:
        return "WARN"
    return "PASS" if all(checks) else "FAIL"


def metrics(ctx: RunContext, connector: str, pg_params: Mapping[str, Any],
            ch_params: Mapping[str, Any]) -> dict[str, dict[str, Any]]:  # fmt: skip
    """Legacy compute_metrics: base metrics per stage, then composites in order."""
    config = defs.CONNECTOR_METRICS.get(connector, {})
    stages: dict[str, dict[str, Any]] = {"raw": {}, "psql": {}, "ckh": {}}
    errors: dict[str, str] = {}
    for name, d in config.get("metrics", {}).items():
        if d.get("raw_sql"):
            stages["raw"][name], err = _try(_pg, ctx, d["raw_sql"], pg_params)
            if err:
                errors[f"raw_{name}"] = err
        stages["psql"][name], err = _try(_pg, ctx, d["psql_sql"], pg_params)
        if err:
            errors[f"psql_{name}"] = err
        stages["ckh"][name], err = _try(_ch, ctx, d["ckh_sql"], ch_params)
        if err:
            errors[f"ckh_{name}"] = err
    for name, comp in config.get("composites", {}).items():
        for stage, values in stages.items():
            try:
                values[name] = comp["fn"](values)
            except Exception as exc:  # noqa: BLE001 - legacy records the error and goes on
                values[name], errors[f"{stage}_{name}"] = None, type(exc).__name__
    out: dict[str, dict[str, Any]] = {}
    for name in config.get("display_order", []):
        values = [stages[s].get(name) for s in ("raw", "psql", "ckh")]
        errs = [errors.get(f"{s}_{name}") for s in ("raw", "psql", "ckh")]
        out[name] = {"raw": values[0], "psql": values[1], "ckh": values[2],
                     "verdict": verdict(values, errs),
                     "errors": [e for e in errs if e]}  # fmt: skip
    return out


def shopify_api_count(ctx: RunContext, org: Org, day: date) -> int:
    """Legacy fetch_shopify_api_order_count: orders created that IST day, any status."""
    start = datetime.combine(day, time(), IST)
    end = start + timedelta(days=1) - timedelta(seconds=1)
    with connect(ctx, org) as client:
        url = f"{client.shop.url}/admin/api/{client.shop.api_version}/orders/count.json"
        params = {"created_at_min": start.isoformat(), "created_at_max": end.isoformat(),
                  "status": "any"}  # fmt: skip
        headers = {"X-Shopify-Access-Token": client.shop.token}
        response = client.http.get(url, params=params, headers=headers)
        response.raise_for_status()
        return int(response.json().get("count", 0))


def pipeline_counts(ctx: RunContext, org: Org, connector: str, day: date,
                    pg_params: Mapping[str, Any],
                    ch_params: Mapping[str, Any]) -> dict[str, Any]:  # fmt: skip
    cfg = defs.PIPELINE_COUNT_CONFIG.get(connector)
    if not cfg:
        return {}
    counts: dict[str, Any] = {"label": cfg.get("label", connector)}
    if connector == "shopify":
        counts["api"], counts["api_error"] = _try(shopify_api_count, ctx, org, day)
    counts["raw"], counts["raw_error"] = _try(_pg, ctx, cfg["raw_sql"], pg_params)
    counts["psql"], counts["psql_error"] = _try(_pg, ctx, cfg["psql_sql"], pg_params)
    counts["ckh"], counts["ckh_error"] = _try(_ch, ctx, cfg["ckh_sql"], ch_params)
    return {k: v for k, v in counts.items() if v is not None}


def render(org: str, connector: str, day: date, result: Mapping[str, Any]) -> str:
    lines = [f":bar_chart: Reliability `{org}` / {connector} / {day.isoformat()}"]
    counts = result["pipeline"]
    if counts:
        stages = " -> ".join(f"{k} {counts[k]:,.0f}" for k in ("api", "raw", "psql", "ckh")
                             if k in counts)  # fmt: skip
        lines.append(f"{counts.get('label', '')}: {stages}")
    for name, m in result["metrics"].items():
        if m["verdict"] in ("FAIL", "ERR"):
            lines.append(f"- {m['verdict']} {name}: raw {m['raw']} / psql {m['psql']}"
                         f" / ckh {m['ckh']} {', '.join(m['errors'])}")  # fmt: skip
    return "\n".join(lines)


def report(ctx: RunContext, org: Org, window: Window) -> StepResult:
    connector = CONNECTOR[org.source]
    detail: dict[str, Any] = {}
    for day in report_days(window):
        start = datetime.combine(day, time())  # IST wall clock, as the tables keep it
        pg_params = {"org_id": org.org_id, "start": start, "end": start + timedelta(days=1)}
        ch_params = {"org": org.org_id, "start": start, "end": start + timedelta(days=1)}
        result = {"pipeline": pipeline_counts(ctx, org, connector, day, pg_params, ch_params),
                  "metrics": metrics(ctx, connector, pg_params, ch_params)}  # fmt: skip
        detail[day.isoformat()] = result
        if any(m["verdict"] in ("FAIL", "ERR") for m in result["metrics"].values()):
            post_message(ctx.settings.alerts, render(org.org_id, connector, day, result))
    return StepResult(detail=detail)


def _pipeline(name: str, source: str) -> Pipeline:
    return Pipeline(name=name, source=source, steps=(Step("report", report),),
                    lookback=timedelta(0), first_window=timedelta(days=1),
                    timeout=timedelta(minutes=30), schedule="30 5 * * *")  # fmt: skip


RELIABILITY_SHOPIFY = _pipeline("reliability-shopify", "shopify")
RELIABILITY_META = _pipeline("reliability-meta", "meta_ads")
RELIABILITY_GOOGLE = _pipeline("reliability-google", "google_ads")
