# Graph API payloads are untyped JSON; these rows read them as legacy did.
# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false
"""Meta rows for the raw tables, column for column as legacy's loaders built them
(load_meta_ads_raw.py, load_meta_ads_creative_raw.py, load_meta_ads_structure_raw.py,
load_meta_ads_region_raw.py), and their upsert keys."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from psycopg import Connection

from etl.core.pg_load import upsert
from etl.sources.meta.india_states import canonical_state

INSIGHT_FIELDS = ",".join([
    "date_start", "date_stop", "impressions", "clicks", "spend", "reach", "frequency",
    "actions", "action_values", "ad_id", "ad_name", "adset_id", "adset_name", "campaign_id",
    "campaign_name", "account_id", "account_name", "objective", "video_play_actions",
    "video_p25_watched_actions", "video_p50_watched_actions", "video_p75_watched_actions",
    "video_p95_watched_actions", "video_p100_watched_actions", "video_avg_time_watched_actions",
    "video_30_sec_watched_actions", "cost_per_thruplay", "cost_per_15_sec_video_view",
])  # fmt: skip
REGION_FIELDS = ("date_start,date_stop,spend,impressions,clicks,reach,campaign_id,campaign_name,"
                 "account_id,account_name")  # fmt: skip
INSIGHT_COLUMNS = (
    "organization_id", "job_id", "date_start", "date_stop", "impressions", "clicks", "spend",
    "reach", "frequency", "actions", "action_values", "ad_id", "ad_name", "adset_id",
    "adset_name", "campaign_id", "campaign_name", "account_id", "account_name", "objective",
    "video_plays", "video_p25", "video_p50", "video_p75", "video_p95", "video_p100",
    "video_avg_watch_time", "video_30s", "cost_per_thruplay", "cost_per_15s_view",
)  # fmt: skip
CREATIVE_COLUMNS = (
    "organization_id", "job_id", "ad_id", "ad_name", "creative_id", "creative_name", "body",
    "title", "image_url", "thumbnail_url", "video_id", "object_story_spec", "asset_feed_spec",
    "call_to_action_type", "creative_status", "object_type", "url_tags",
    "instagram_permalink_url", "effective_object_story_id", "effective_status",
    "original_image_url", "video_source_url", "video_mime_type", "video_duration_seconds",
    "video_url_fetched_at", "ad_group_status", "campaign_status", "call_to_action", "post_link",
)  # fmt: skip
# Legacy's upsert kept these when the new fetch had none.
CREATIVE_KEEP = ("original_image_url", "video_url_fetched_at", "post_link")
CAMPAIGN_COLUMNS = (
    "organization_id", "job_id", "account_id", "campaign_id", "name", "status",
    "effective_status", "objective", "daily_budget", "lifetime_budget", "bid_strategy",
    "start_time", "stop_time", "updated_time", "fetched_at",
)  # fmt: skip
ADSET_COLUMNS = (
    "organization_id", "job_id", "account_id", "adset_id", "name", "campaign_id", "status",
    "effective_status", "optimization_goal", "billing_event", "bid_strategy", "daily_budget",
    "lifetime_budget", "start_time", "end_time", "updated_time", "learning_stage_status",
    "learning_last_sig_edit_ts", "fetched_at",
)  # fmt: skip
AD_COLUMNS = (
    "organization_id", "job_id", "account_id", "ad_id", "name", "adset_id", "campaign_id",
    "status", "effective_status", "creative_id", "updated_time", "fetched_at",
)  # fmt: skip
REGION_COLUMNS = (
    "organization_id", "job_id", "date_start", "date_stop", "region", "state", "campaign_id",
    "campaign_name", "account_id", "account_name", "impressions", "clicks", "reach", "spend",
    "loaded_at",
)  # fmt: skip


def _first_value(actions: Any, cast: Callable[[Any], Any] = int) -> Any:
    """The first value of a Meta action array, e.g. [{"action_type": ..., "value": "123"}]."""
    if not actions or not isinstance(actions, list):
        return None
    try:
        return cast(actions[0].get("value", 0))
    except (ValueError, TypeError, IndexError, AttributeError):
        return None


def insight_row(org: str, job: str, r: Mapping[str, Any]) -> list[Any]:
    return [
        org, job, r.get("date_start"), r.get("date_stop"), int(r.get("impressions") or 0),
        int(r.get("clicks") or 0), float(r.get("spend") or 0.0),
        int(r["reach"]) if r.get("reach") is not None else None,
        float(r["frequency"]) if r.get("frequency") is not None else None,
        json.dumps(r.get("actions") or []), json.dumps(r.get("action_values") or []),
        r.get("ad_id"), r.get("ad_name"), r.get("adset_id"), r.get("adset_name"),
        r.get("campaign_id"), r.get("campaign_name"), r.get("account_id"), r.get("account_name"),
        r.get("objective"),
        *(_first_value(r.get(k)) for k in (
            "video_play_actions", "video_p25_watched_actions", "video_p50_watched_actions",
            "video_p75_watched_actions", "video_p95_watched_actions", "video_p100_watched_actions",
            "video_avg_time_watched_actions", "video_30_sec_watched_actions")),
        _first_value(r.get("cost_per_thruplay"), float),
        _first_value(r.get("cost_per_15_sec_video_view"), float),
    ]  # fmt: skip


def creative_row(org: str, job: str, r: Mapping[str, Any], now: datetime) -> list[Any]:
    source = r.get("video_source_url")
    cta = r.get("call_to_action")
    return [
        org, job, r.get("ad_id"), r.get("ad_name"), r.get("creative_id"), r.get("creative_name"),
        r.get("body"), r.get("title"), r.get("image_url"), r.get("thumbnail_url"),
        r.get("video_id"), json.dumps(r.get("object_story_spec") or {}),
        json.dumps(r.get("asset_feed_spec") or {}), r.get("call_to_action_type"),
        r.get("status"), r.get("object_type"), r.get("url_tags"),
        r.get("instagram_permalink_url"), r.get("effective_object_story_id"),
        r.get("effective_status"), r.get("original_image_url"), source,
        r.get("video_mime_type"), r.get("video_duration_seconds"), now if source else None,
        r.get("ad_group_status"), r.get("campaign_status"),
        json.dumps(cta) if cta is not None else None, r.get("post_link"),
    ]  # fmt: skip


def campaign_row(org: str, job: str, r: Mapping[str, Any], now: datetime) -> list[Any]:
    return [org, job, r.get("account_id"), r.get("id"), r.get("name"), r.get("status"),
            r.get("effective_status"), r.get("objective"), r.get("daily_budget"),
            r.get("lifetime_budget"), r.get("bid_strategy"), r.get("start_time"),
            r.get("stop_time"), r.get("updated_time"), now]  # fmt: skip


def adset_row(org: str, job: str, r: Mapping[str, Any], now: datetime) -> list[Any]:
    learning = r.get("learning_stage_info") or {}
    last_sig = learning.get("last_sig_edit_ts")
    return [org, job, r.get("account_id"), r.get("id"), r.get("name"), r.get("campaign_id"),
            r.get("status"), r.get("effective_status"), r.get("optimization_goal"),
            r.get("billing_event"), r.get("bid_strategy"), r.get("daily_budget"),
            r.get("lifetime_budget"), r.get("start_time"), r.get("end_time"),
            r.get("updated_time"), learning.get("status"),
            str(last_sig) if last_sig is not None else None, now]  # fmt: skip


def ad_row(org: str, job: str, r: Mapping[str, Any], now: datetime) -> list[Any]:
    return [org, job, r.get("account_id"), r.get("id"), r.get("name"), r.get("adset_id"),
            r.get("campaign_id"), r.get("status"), r.get("effective_status"),
            (r.get("creative") or {}).get("id"), r.get("updated_time"), now]  # fmt: skip


def region_row(org: str, job: str, r: Mapping[str, Any], now: datetime) -> list[Any]:
    region = (r.get("region") or "Unknown").strip() or "Unknown"
    return [org, job, r.get("date_start"), r.get("date_stop"), region, canonical_state(region),
            r.get("campaign_id"), r.get("campaign_name"), r.get("account_id"),
            r.get("account_name"), int(r.get("impressions") or 0), int(r.get("clicks") or 0),
            int(r["reach"]) if r.get("reach") is not None else None,
            float(r.get("spend") or 0.0), now]  # fmt: skip


def now_utc() -> datetime:
    return datetime.now(UTC)


def load(conn: Connection[Any], table: str, columns: Sequence[str], key: Sequence[str],
         rows: Sequence[Sequence[Any]], keep: Sequence[str] = ()) -> int:  # fmt: skip
    """Upsert in the caller's transaction (legacy committed once per loader)."""
    return upsert(conn, table, columns, key, rows, keep_existing=keep)
