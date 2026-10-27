"""Google Ads datasets: GAQL, the row mapping, and the raw table each lands in, copied from
legacy (fetchers/google_ads_fetcher.py, load_google_ads_raw.py). Legacy quirks kept, e.g. the
ad performance mapping reads adGroup.name/status that its query does not select (always NULL).
"""

from __future__ import annotations

# Google Ads payloads are untyped JSON; read as legacy did.
# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, LiteralString

Row = dict[str, Any]

_ADPERF = """SELECT ad_group_ad.ad.id, ad_group_ad.ad.name, ad_group.id, ad_group_ad.status,
  ad_group_ad.primary_status,
  campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type,
  campaign.advertising_channel_sub_type, customer.id, metrics.impressions, metrics.clicks,
  metrics.cost_micros, metrics.conversions, metrics.conversions_value,
  metrics.video_trueview_views, metrics.video_quartile_p25_rate, metrics.video_quartile_p50_rate,
  metrics.video_quartile_p75_rate, metrics.video_quartile_p100_rate,
  metrics.video_trueview_view_rate, segments.date
FROM ad_group_ad WHERE segments.date >= '{start}' AND segments.date < '{end}'"""
_CAMPAIGN = """SELECT campaign.id, campaign.name, campaign.status, customer.id,
  campaign.advertising_channel_type, campaign.advertising_channel_sub_type, metrics.impressions,
  metrics.clicks, metrics.cost_micros, metrics.conversions_value, segments.date
FROM campaign WHERE segments.date >= '{start}' AND segments.date < '{end}'"""
_ADGROUP = """SELECT ad_group.id, ad_group.name, ad_group.status, ad_group.campaign, segments.date
FROM ad_group WHERE segments.date >= '{start}' AND segments.date < '{end}'"""
_CAP = """SELECT ad_group_ad.ad.id, segments.conversion_action, metrics.all_conversions,
  metrics.all_conversions_value, metrics.conversions, metrics.conversions_value, segments.date
FROM ad_group_ad WHERE segments.date >= '{start}' AND segments.date < '{end}'
  AND segments.conversion_action IS NOT NULL"""
_CCAP = """SELECT campaign.id, segments.conversion_action, metrics.all_conversions,
  metrics.all_conversions_value, metrics.conversions, metrics.conversions_value, segments.date
FROM campaign WHERE segments.date >= '{start}' AND segments.date < '{end}'
  AND segments.conversion_action IS NOT NULL"""
_SHOPPING = """SELECT campaign.id, campaign.name, campaign.advertising_channel_type, ad_group.id,
  ad_group.name, customer.id, segments.product_item_id, segments.product_title,
  metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions,
  metrics.conversions_value, segments.date
FROM shopping_performance_view WHERE segments.date >= '{start}' AND segments.date < '{end}'
  AND campaign.advertising_channel_type != 'PERFORMANCE_MAX'"""
_PMAX = """SELECT campaign.id, campaign.name, campaign.advertising_channel_type, customer.id,
  segments.product_item_id, segments.product_title, metrics.impressions, metrics.clicks,
  metrics.cost_micros, metrics.conversions, metrics.conversions_value, segments.date
FROM shopping_performance_view WHERE campaign.advertising_channel_type = 'PERFORMANCE_MAX'
  AND segments.date >= '{start}' AND segments.date < '{end}'"""
CONVERSION_ACTIONS = """SELECT conversion_action.resource_name, conversion_action.name,
  conversion_action.category, conversion_action.status FROM conversion_action"""
AD_CREATIVES = """SELECT ad_group_ad.ad.id, ad_group_ad.ad.name, ad_group_ad.ad.type,
  ad_group_ad.ad.final_urls, ad_group_ad.ad_strength, ad_group_ad.ad.responsive_search_ad.headlines,
  ad_group_ad.ad.responsive_search_ad.descriptions, ad_group_ad.ad.responsive_search_ad.path1,
  ad_group_ad.ad.responsive_search_ad.path2, ad_group_ad.ad.responsive_display_ad.headlines,
  ad_group_ad.ad.responsive_display_ad.long_headline,
  ad_group_ad.ad.responsive_display_ad.descriptions,
  ad_group_ad.ad.responsive_display_ad.business_name,
  ad_group_ad.ad.responsive_display_ad.call_to_action_text, ad_group_ad.ad.image_ad.image_url,
  ad_group_ad.ad.responsive_display_ad.marketing_images,
  ad_group_ad.ad.responsive_display_ad.square_marketing_images,
  ad_group_ad.ad.demand_gen_multi_asset_ad.marketing_images,
  ad_group_ad.ad.demand_gen_video_responsive_ad.videos, ad_group_ad.ad.app_ad.images,
  ad_group_ad.ad.video_ad.video.asset, ad_group_ad.ad.video_responsive_ad.videos,
  ad_group_ad.ad.display_url, ad_group_ad.status, campaign.id, campaign.advertising_channel_type
FROM ad_group_ad WHERE ad_group_ad.status != 'REMOVED'"""
# The pictures behind the asset-based ads: one query per account, joined in Python (an
# ad row only names the asset). YouTube videos give a video id; the thumbnail is i.ytimg.com.
ASSETS = """SELECT asset.resource_name, asset.type, asset.image_asset.full_size.url,
  asset.youtube_video_asset.youtube_video_id
FROM asset WHERE asset.type IN ('IMAGE', 'YOUTUBE_VIDEO')"""
CLICK_VIEW = (
    "SELECT click_view.gclid, click_view.ad_group_ad, campaign.id, ad_group.id, customer.id,"
    " segments.date FROM click_view WHERE segments.date = '{day}'"
)


def _int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def report_date(value: Any, now: datetime) -> datetime:
    """Legacy _parse_dt: the day at 00:00 UTC; a missing or bad date is "now"."""
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return now


def _m(r: Row) -> Row:
    return r.get("metrics", {}) or {}


def _both(r: Row, all_key: str, key: str) -> Any:
    value = _m(r).get(all_key)
    return value if value is not None else _m(r).get(key)


def adperf(r: Row) -> Row:
    c, ag, ad = r.get("campaign", {}), r.get("adGroup", {}), r.get("adGroupAd", {})
    m = _m(r)
    return {"customer_id": r.get("customer", {}).get("id"), "campaign_id": c.get("id"),
            "campaign_name": c.get("name"), "campaign_status": c.get("status"),
            "advertising_channel_type": c.get("advertisingChannelType"),
            "advertising_channel_sub_type": c.get("advertisingChannelSubType"),
            "ad_group_id": ag.get("id"), "ad_group_name": ag.get("name"),
            "ad_group_status": ag.get("status"), "ad_id": ad.get("ad", {}).get("id"),
            "ad_name": ad.get("ad", {}).get("name"), "ad_status": ad.get("status"),
            # The serving status (ELIGIBLE, LIMITED, PAUSED, NOT_ELIGIBLE...), like Meta's
            # effective_status; the transform writes it to advertising.ad_status.
            "ad_primary_status": ad.get("primaryStatus"),
            "impressions": m.get("impressions"), "clicks": m.get("clicks"),
            "cost_micros": m.get("costMicros"), "conversions": m.get("conversions"),
            "conversions_value": m.get("conversionsValue"),
            "video_views": m.get("videoTrueviewViews"),
            "video_quartile_p25_rate": m.get("videoQuartileP25Rate"),
            "video_quartile_p50_rate": m.get("videoQuartileP50Rate"),
            "video_quartile_p75_rate": m.get("videoQuartileP75Rate"),
            "video_quartile_p100_rate": m.get("videoQuartileP100Rate"),
            "video_view_rate": m.get("videoTrueviewViewRate"),
            "date": r.get("segments", {}).get("date")}  # fmt: skip


def campaign(r: Row) -> Row:
    c, m = r.get("campaign", {}), _m(r)
    revenue = m.get("conversionsValueMicros") or int(float(m.get("conversionsValue") or 0) * 1e6)
    return {"id": c.get("id"), "name": c.get("name"), "status": c.get("status"),
            "customer_id": r.get("customer", {}).get("id"),
            "advertising_channel_type": c.get("advertisingChannelType"),
            "advertising_channel_sub_type": c.get("advertisingChannelSubType"),
            "impressions": m.get("impressions"), "clicks": m.get("clicks"),
            "cost_micros": m.get("costMicros"), "revenue_micros": revenue,
            "date": r.get("segments", {}).get("date")}  # fmt: skip


def adgroup(r: Row) -> Row:
    ag = r.get("adGroup", {})
    return {"id": ag.get("id"), "name": ag.get("name"), "status": ag.get("status"),
            "campaign": ag.get("campaign"), "date": r.get("segments", {}).get("date")}  # fmt: skip


def cap(r: Row) -> Row:
    return {"ad_id": r.get("adGroupAd", {}).get("ad", {}).get("id"),
            "conversion_action": r.get("segments", {}).get("conversionAction"),
            "conversions": _both(r, "allConversions", "conversions"),
            "conversions_value": _both(r, "allConversionsValue", "conversionsValue"),
            "date": r.get("segments", {}).get("date")}  # fmt: skip


def ccap(r: Row) -> Row:
    return {"campaign_id": r.get("campaign", {}).get("id"),
            "conversion_action": r.get("segments", {}).get("conversionAction"),
            "conversions": _both(r, "allConversions", "conversions"),
            "conversions_value": _both(r, "allConversionsValue", "conversionsValue"),
            "date": r.get("segments", {}).get("date")}  # fmt: skip


def product_perf(entity_key: str, id_field: str, name_field: str) -> Callable[[Row], Row]:
    def norm(r: Row) -> Row:
        entity, c, m = r.get(entity_key, {}) or {}, r.get("campaign", {}), _m(r)
        return {"customer_id": r.get("customer", {}).get("id"), "campaign_id": c.get("id"),
                "campaign_name": c.get("name"),
                "advertising_channel_type": c.get("advertisingChannelType"),
                id_field: entity.get("id"), name_field: entity.get("name"),
                "product_item_id": r.get("segments", {}).get("productItemId"),
                "product_title": r.get("segments", {}).get("productTitle"),
                "impressions": m.get("impressions"), "clicks": m.get("clicks"),
                "cost_micros": m.get("costMicros"), "conversions": m.get("conversions"),
                "conversions_value": m.get("conversionsValue"),
                "date": r.get("segments", {}).get("date")}  # fmt: skip

    return norm


@dataclass(frozen=True)
class Dataset:
    name: str
    query: str
    normalize: Callable[[Row], Row]
    table: str
    columns: tuple[str, ...]
    key: tuple[str, ...]
    chunk_days: int = 30
    conflict: LiteralString | None = None
    optional: bool = False  # legacy: a failure is logged, the run goes on


_PERF = ("organization_id", "job_id", "customer_id", "campaign_id", "campaign_name",
         "advertising_channel_type")  # fmt: skip
_METRICS = ("product_item_id", "product_title", "impressions", "clicks", "cost_micros",
            "conversions", "conversions_value", "__hevo_report_date")  # fmt: skip
DATED: tuple[Dataset, ...] = (
    Dataset("ad_performance", _ADPERF, adperf, "raw_google_ads_ad_group_ad",
            ("organization_id", "job_id", "customer_id", "campaign_id", "campaign_name",
             "campaign_status", "advertising_channel_type", "advertising_channel_sub_type",
             "ad_group_id", "ad_group_name", "ad_group_status", "ad_id", "ad_name", "ad_status",
             "ad_primary_status", "impressions", "clicks", "cost_micros", "conversions",
             "conversions_value", "__hevo_report_date", "video_views", "video_quartile_p25_rate",
             "video_quartile_p50_rate", "video_quartile_p75_rate", "video_quartile_p100_rate",
             "video_view_rate"),
            ("organization_id", "customer_id", "ad_id", "__hevo_report_date")),
    Dataset("campaign_snapshot", _CAMPAIGN, campaign, "raw_google_ads_campaign",
            ("id", "organization_id", "job_id", "name", "status", "customer_id",
             "advertising_channel_type", "advertising_channel_sub_type", "impressions", "clicks",
             "cost_micros", "revenue_micros", "__hevo_report_date"),
            ("organization_id", "id", "__hevo_report_date")),
    Dataset("ad_group_snapshot", _ADGROUP, adgroup, "raw_google_ads_ad_group",
            ("id", "organization_id", "job_id", "name", "status", "campaign", "__hevo_report_date"),
            ("organization_id", "id", "__hevo_report_date")),
    Dataset("conversion_action_perf", _CAP, cap, "raw_google_ads_conv_action_perf",
            ("ad_id", "organization_id", "job_id", "conversion_action", "conversions",
             "conversions_value", "__hevo_report_date"),
            ("organization_id", "ad_id", "conversion_action", "__hevo_report_date")),
    Dataset("campaign_conv_action_perf", _CCAP, ccap, "raw_google_ads_campaign_conv_action_perf",
            ("campaign_id", "organization_id", "job_id", "conversion_action", "conversions",
             "conversions_value", "__hevo_report_date"),
            ("organization_id", "campaign_id", "conversion_action", "__hevo_report_date")),
    Dataset("shopping_product_perf", _SHOPPING, product_perf("adGroup", "ad_group_id",
            "ad_group_name"), "raw_google_ads_shopping_perf",
            (*_PERF, "ad_group_id", "ad_group_name", *_METRICS),
            ("organization_id", "customer_id", "campaign_id", "ad_group_id", "product_item_id",
             "__hevo_report_date"), chunk_days=7,
            conflict=("organization_id, customer_id, campaign_id, COALESCE(ad_group_id, 0),"
                      " COALESCE(product_item_id, ''), __hevo_report_date"), optional=True),
    Dataset("asset_group_product_perf", _PMAX, product_perf("assetGroup", "asset_group_id",
            "asset_group_name"), "raw_google_ads_asset_group_product_perf",
            (*_PERF, "asset_group_id", "asset_group_name", *_METRICS),
            ("organization_id", "customer_id", "campaign_id", "asset_group_id", "product_item_id",
             "__hevo_report_date"), chunk_days=7,
            conflict=("organization_id, customer_id, campaign_id, COALESCE(asset_group_id, 0),"
                      " COALESCE(product_item_id, ''), __hevo_report_date"), optional=True),
)  # fmt: skip
_INTS = {"campaign_id", "ad_group_id", "ad_id", "impressions", "clicks", "cost_micros", "id",
         "revenue_micros", "video_views", "asset_group_id"}  # fmt: skip
_FLOATS = {"conversions", "conversions_value", "video_quartile_p25_rate", "video_quartile_p50_rate",
           "video_quartile_p75_rate", "video_quartile_p100_rate", "video_view_rate"}  # fmt: skip


def raw_row(dataset: Dataset, org: str, job: str, n: Row, now: datetime) -> list[Any]:
    """Legacy's loader row: ids and counts as int, rates and conversions as float."""
    values = {**n, "organization_id": org, "job_id": job,
              "__hevo_report_date": report_date(n.get("date"), now)}  # fmt: skip
    out: list[Any] = []
    for column in dataset.columns:
        v = values.get(column)
        out.append(_int(v) if column in _INTS else _float(v) if column in _FLOATS else v)
    return out


def conversion_action_row(r: Row) -> list[Any]:
    ca = r.get("conversionAction", {})
    return [ca.get("resourceName"), ca.get("name"), ca.get("category"), ca.get("status")]


CREATIVE_COLUMNS = (
    "ad_id", "organization_id", "job_id", "ad_name", "ad_type", "final_urls", "ad_strength",
    "responsive_search_ad", "responsive_display_ad", "image_ad_image_url", "video_ad_video",
    "display_url", "ad_status", "campaign_id", "advertising_channel_type", "image_url",
    "youtube_video_id",
)  # fmt: skip

# An asset's picture: the image URL, or the YouTube video id (a thumbnail is made from it).
Assets = dict[str, tuple[str | None, str | None]]
_IMAGE_LISTS = (("responsiveDisplayAd", "marketingImages"),
                ("responsiveDisplayAd", "squareMarketingImages"),
                ("demandGenMultiAssetAd", "marketingImages"), ("appAd", "images"))  # fmt: skip
_VIDEO_LISTS = (("videoResponsiveAd", "videos"), ("demandGenVideoResponsiveAd", "videos"))


def asset_entry(r: Row) -> tuple[str, tuple[str | None, str | None]] | None:
    a = r.get("asset", {})
    name = a.get("resourceName")
    if not name:
        return None
    image = ((a.get("imageAsset") or {}).get("fullSize") or {}).get("url")
    video = (a.get("youtubeVideoAsset") or {}).get("youtubeVideoId")
    return str(name), (str(image) if image else None, str(video) if video else None)


def _asset_names(ad: Row, lists: tuple[tuple[str, str], ...]) -> list[str]:
    names: list[str] = []
    for kind, field in lists:
        for item in (ad.get(kind) or {}).get(field) or []:
            if isinstance(item, dict) and item.get("asset"):
                names.append(str(item["asset"]))
    return names


def picture(ad: Row, assets: Assets) -> tuple[str | None, str | None, str | None]:
    """(image_url, youtube_video_id, video asset) of an ad. Image ads carry their URL; display,
    Demand Gen and app ads name image assets; video ads name a YouTube asset. Search and
    Shopping ads have no picture in the API."""
    video_assets = [n for n in _asset_names(ad, _VIDEO_LISTS)]
    single = ((ad.get("videoAd") or {}).get("video") or {}).get("asset")
    if single:
        video_assets.insert(0, str(single))
    video_id = next((assets[n][1] for n in video_assets if n in assets and assets[n][1]), None)
    image = (ad.get("imageAd") or {}).get("imageUrl")
    if not image:
        image = next((assets[n][0] for n in _asset_names(ad, _IMAGE_LISTS)
                      if n in assets and assets[n][0]), None)  # fmt: skip
    if not image and video_id:
        image = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    return (str(image) if image else None, video_id, video_assets[0] if video_assets else None)


def creative_row(org: str, job: str, r: Row, assets: Assets | None = None) -> list[Any]:
    ad = r.get("adGroupAd", {}).get("ad", {})
    image, video_id, video_asset = picture(ad, assets or {})
    return [_int(ad.get("id")), org, job, ad.get("name"), ad.get("type"),
            json.dumps(ad.get("finalUrls") or []), r.get("adGroupAd", {}).get("adStrength"),
            json.dumps(ad.get("responsiveSearchAd") or {}),
            json.dumps(ad.get("responsiveDisplayAd") or {}),
            (ad.get("imageAd") or {}).get("imageUrl"), video_asset, ad.get("displayUrl"),
            r.get("adGroupAd", {}).get("status"), _int(r.get("campaign", {}).get("id")),
            r.get("campaign", {}).get("advertisingChannelType"), image, video_id]  # fmt: skip


CLICK_COLUMNS = ("organization_id", "gclid", "click_date", "customer_id", "campaign_id",
                 "ad_group_id", "ad_id")  # fmt: skip


def click_row(org: str, day: str, r: Row) -> list[Any] | None:
    cv = r.get("clickView", {}) or {}
    if not cv.get("gclid"):
        return None
    ad_res = cv.get("adGroupAd") or ""
    ad_id = ad_res.rsplit("~", 1)[-1] if "~" in ad_res else None

    def text(v: Any) -> str | None:
        return str(v) if v is not None else None

    try:
        click_date = date.fromisoformat(str(r.get("segments", {}).get("date") or day)[:10])
    except ValueError:
        return None
    ids = [text(r.get(k, {}).get("id")) for k in ("customer", "campaign", "adGroup")]
    return [org, cv["gclid"], click_date, *ids, ad_id]
