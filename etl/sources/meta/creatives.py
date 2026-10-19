# Graph API payloads are untyped JSON; these modules read them as legacy did.
# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false
"""Meta ad creatives for the ads seen in insights (legacy fetchers/meta_ads_creative_fetcher.py,
copied phase by phase).

1. `/ads` with the creative expanded inline, the ad ids in batches of 50 (`id IN`).
2. Image hashes -> full-size URLs through `/adimages`.
3. Video poster frame, source, mime type and length through `/advideos`.
4. A creative with no link of its own and an existing Page post: the post's call_to_action
   link, read with that Page's token (`post_link`).
Phases 2-4 skip a failed batch, as legacy did. Legacy's loader then queued Creative
Intelligence jobs; v2 does not during parity (the legacy worker turns them into Gemini spend).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any

from etl.core.logging import get_logger
from etl.sources.meta.graph import Graph

log = get_logger(__name__)
AD_CREATIVE_FIELDS = ",".join([
    "id", "name", "body", "title", "image_url", "image_hash", "thumbnail_url", "video_id",
    "object_story_spec", "asset_feed_spec", "call_to_action_type", "call_to_action", "status",
    "object_type", "url_tags", "instagram_permalink_url", "effective_object_story_id",
    "object_story_id",
])  # fmt: skip
ADS_FIELDS = ("id,name,effective_status,adset{id,effective_status},campaign{id,effective_status},"
              "creative{" + AD_CREATIVE_FIELDS + "}")  # fmt: skip
BATCH = 50
_LINK_PATHS: tuple[tuple[str | int, ...], ...] = (
    ("object_story_spec", "video_data", "call_to_action", "value", "link"),
    ("object_story_spec", "link_data", "call_to_action", "value", "link"),
    ("object_story_spec", "link_data", "link"),
    ("object_story_spec", "template_data", "link"),
    ("asset_feed_spec", "link_urls", 0, "website_url"),
    ("call_to_action", "value", "link"),
)


def _batches(items: Sequence[str], size: int = BATCH) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield list(items[i : i + size])


def image_hashes(row: dict[str, Any]) -> list[str]:
    """Hashes in priority order: creative, link_data, carousel children (legacy
    fetchers/_meta_creative_hashes.py)."""
    ordered: list[str] = []
    oss = row.get("object_story_spec") or {}
    link_data = oss.get("link_data") or {} if isinstance(oss, dict) else {}
    children = [c for c in link_data.get("child_attachments") or [] if isinstance(c, dict)]
    for h in [row.get("image_hash"), link_data.get("image_hash"),
              *(c.get("image_hash") for c in children)]:  # fmt: skip
        if h and h not in ordered:
            ordered.append(h)
    return ordered


def creative_link(record: dict[str, Any]) -> Any:
    for path in _LINK_PATHS:
        cur: Any = record
        for key in path:
            if isinstance(key, int):
                cur = cur[key] if isinstance(cur, list) and len(cur) > key else None
            else:
                cur = cur.get(key) if isinstance(cur, dict) else None
            if cur is None:
                break
        if cur:
            return cur
    return None


def _record(ad: dict[str, Any], creative: dict[str, Any]) -> dict[str, Any]:
    keys = ("body", "title", "image_url", "image_hash", "thumbnail_url", "video_id",
            "object_story_spec", "asset_feed_spec", "call_to_action_type", "call_to_action",
            "status", "object_type", "url_tags", "instagram_permalink_url",
            "effective_object_story_id", "object_story_id")  # fmt: skip
    return {"ad_id": ad.get("id"), "ad_name": ad.get("name"),
            "effective_status": ad.get("effective_status"),
            "ad_group_status": (ad.get("adset") or {}).get("effective_status"),
            "campaign_status": (ad.get("campaign") or {}).get("effective_status"),
            "creative_id": creative.get("id"), "creative_name": creative.get("name"),
            **{k: creative.get(k) for k in keys}, "original_image_url": None,
            "post_link": None, "video_source_url": None, "video_mime_type": None,
            "video_duration_seconds": None}  # fmt: skip


def fetch_creatives(graph: Graph, account: str, ad_ids: Iterable[str],
                    page_tokens: dict[str, str]) -> list[dict[str, Any]]:  # fmt: skip
    records: list[dict[str, Any]] = []
    for batch in _batches(sorted(set(ad_ids))):
        filtering = json.dumps([{"field": "id", "operator": "IN", "value": batch}])
        params = {"fields": ADS_FIELDS, "limit": 25, "filtering": filtering}
        for page in graph.paged(f"{account}/ads", params, pause=0.3):
            for ad in page:
                creative = ad.get("creative") or {}
                if creative.get("id"):
                    records.append(_record(ad, creative))
    _resolve_images(graph, account, records)
    _resolve_videos(graph, account, records)
    if page_tokens:
        resolve_post_links(graph, records, page_tokens)
    return records


def _resolve_images(graph: Graph, account: str, records: list[dict[str, Any]]) -> None:
    hashes = sorted({h for r in records for h in image_hashes(r)})
    found: dict[str, str] = {}
    for batch in _batches(hashes):
        try:
            body = graph.get(f"{account}/adimages", {"hashes": json.dumps(batch),
                                                    "fields": "hash,url"})  # fmt: skip
        except Exception as exc:  # noqa: BLE001 - legacy skips a failed batch
            log.warning("meta.adimages_batch_failed", error_type=type(exc).__name__)
            continue
        found |= {
            i["hash"]: i["url"] for i in body.get("data", []) if i.get("hash") and i.get("url")
        }
    for r in records:
        r["original_image_url"] = next((found[h] for h in image_hashes(r) if h in found), None)


def _video_items(body: Any) -> list[tuple[str, dict[str, Any]]]:
    """The edge answers {"data": [...]} or {id: {...}} (legacy handled both)."""
    if not isinstance(body, dict):
        return []
    data = body.get("data")
    if isinstance(data, list):
        return [(str(e["id"]), e) for e in data if isinstance(e, dict) and e.get("id")]
    return [(str(k), v) for k, v in body.items() if isinstance(v, dict)]


def _resolve_videos(graph: Graph, account: str, records: list[dict[str, Any]]) -> None:
    info: dict[str, dict[str, Any]] = {}
    for batch in _batches(sorted({str(r["video_id"]) for r in records if r.get("video_id")})):
        try:
            params = {"ids": ",".join(batch), "fields": "id,picture,source,format,length"}
            body = graph.get(f"{account}/advideos", params)
        except Exception as exc:  # noqa: BLE001 - legacy skips a failed batch
            log.warning("meta.advideos_batch_failed", error_type=type(exc).__name__)
            continue
        for vid, v in _video_items(body):
            formats = v.get("format") or []
            best = formats[-1] if formats and isinstance(formats[-1], dict) else {}
            length = float(v["length"]) if v.get("length") is not None else None
            mime = best.get("mime_type") or "video/mp4"
            info[vid] = {"picture": v.get("picture"), "source": v.get("source"), "mime": mime,
                         "length": length}  # fmt: skip
    for r in records:
        v = info.get(str(r.get("video_id")))
        if not v:
            continue
        if not r.get("original_image_url") and v["picture"]:
            r["original_image_url"] = v["picture"]
        if v["source"]:
            r.update(video_source_url=v["source"], video_mime_type=v["mime"],
                     video_duration_seconds=v["length"])  # fmt: skip


def page_tokens(graph: Graph) -> dict[str, str]:
    """{page_id: page token} from /me/accounts; empty when the token has no Page grants."""
    tokens: dict[str, str] = {}
    try:
        for page in graph.paged("me/accounts", {"fields": "id,access_token", "limit": 100}):
            tokens |= {str(p["id"]): p["access_token"] for p in page
                       if p.get("id") and p.get("access_token")}  # fmt: skip
    except Exception as exc:  # noqa: BLE001 - legacy: no Page access keeps today's behaviour
        log.warning("meta.page_tokens_failed", error_type=type(exc).__name__)
    return tokens


def resolve_post_links(graph: Graph, records: list[dict[str, Any]],
                       tokens: dict[str, str]) -> None:  # fmt: skip
    by_page: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for r in records:
        post_id = str(r.get("object_story_id") or r.get("effective_object_story_id") or "")
        if creative_link(r) or "_" not in post_id:
            continue
        page_id = post_id.split("_", 1)[0]
        if page_id in tokens:
            by_page.setdefault(page_id, {}).setdefault(post_id, []).append(r)
    for page_id, posts in by_page.items():
        for batch in _batches(sorted(posts)):
            try:
                body = graph.get("", {"ids": ",".join(batch), "fields": "call_to_action"},
                                 token=tokens[page_id])  # fmt: skip
            except Exception as exc:  # noqa: BLE001 - legacy skips a failed batch
                log.warning("meta.post_links_batch_failed", error_type=type(exc).__name__)
                continue
            for post_id in batch:
                node = body.get(post_id) or {}
                link = ((node.get("call_to_action") or {}).get("value") or {}).get("link")
                for r in posts[post_id]:
                    r["post_link"] = link or None
