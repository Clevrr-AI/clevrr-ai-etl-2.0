-- Legacy connectors/meta_ads/raw_sanitized_ad.sql, ported by rule only: values are bound
-- (%(org)s, %(start)s, %(end)s: the run's days) instead of pasted text, and a literal percent sign is doubled.
--;;
-- Transform Meta Ads raw insights (JSONB actions) into unified advertising rows (ad-level only)
-- NOTE: Stores only ad-level data. Campaign/adset aggregations are handled by the application layer.
--
-- NO DDL IN THIS FILE — not CREATE TABLE, not CREATE INDEX, not ALTER TABLE. This transform
-- runs once per org inside a 4-way parallel pool; `ALTER TABLE … IF NOT EXISTS` /
-- `CREATE INDEX IF NOT EXISTS` take AccessExclusive/Share locks BEFORE the existence check
-- and deadlocked parallel org runs nightly. Schema lives in
-- ETL/migrations/2026-08-31_001_advertising_columns.sql (applied by utils/migrations_runner.py).

-- Idempotency guards: purge accidental duplicates
WITH dupe_meta AS (
  SELECT id
  FROM (
    SELECT id,
           ROW_NUMBER() OVER (
             PARTITION BY organization_id, source_type, ad_id, created_at
             ORDER BY id DESC
           ) AS rn
    FROM advertising
    -- Org-scoped: an unscoped scan row-locked other orgs' rows under the 4-way parallel
    -- pool; duplicates are per-org by definition (organization_id is in the PARTITION BY).
    WHERE source_type = 'meta_ads' AND ad_id IS NOT NULL
      AND organization_id = %(org)s
  ) t
  WHERE rn > 1
)
DELETE FROM advertising a USING dupe_meta d WHERE a.id = d.id
--;;
-- Clear overlapping rows for the same org + date window to avoid duplication on re-runs
DELETE FROM advertising
WHERE source_type = 'meta_ads'
  AND organization_id = %(org)s
  AND created_at >= (
    SELECT MIN(date_start::timestamp AT TIME ZONE 'UTC')
    FROM raw_meta_ad_insights
    WHERE organization_id = %(org)s AND (date_start::timestamp AT TIME ZONE 'UTC') >= %(start)s AND (date_start::timestamp AT TIME ZONE 'UTC') < %(end)s
  )
  AND created_at <= (
    SELECT MAX(date_start::timestamp AT TIME ZONE 'UTC')
    FROM raw_meta_ad_insights
    WHERE organization_id = %(org)s AND (date_start::timestamp AT TIME ZONE 'UTC') >= %(start)s AND (date_start::timestamp AT TIME ZONE 'UTC') < %(end)s
  )
--;;
WITH base_ads AS (
  SELECT
    r.organization_id,
    'meta_ads' AS source_type,
    r.ad_id::BIGINT AS ad_id,
    r.ad_name,
    r.adset_id::BIGINT AS ad_group_id,
    r.adset_name AS ad_group_name,
    c.effective_status AS ad_status,
    c.ad_group_status AS ad_group_status,
    r.campaign_id::BIGINT AS campaign_id,
    r.campaign_name,
    c.campaign_status AS campaign_status,
    r.account_id,
    r.account_name,
    r.impressions::BIGINT,
    r.clicks::BIGINT,
    COALESCE((SELECT SUM((a->>'value')::NUMERIC) FROM jsonb_array_elements(r.actions) a WHERE a->>'action_type' = 'landing_page_view'),0)::INT AS landing_page_view,
    COALESCE((SELECT SUM((a->>'value')::NUMERIC) FROM jsonb_array_elements(r.actions) a WHERE a->>'action_type' = 'add_to_cart'),0)::INT AS add_to_cart,
    COALESCE((SELECT SUM((a->>'value')::NUMERIC) FROM jsonb_array_elements(r.actions) a WHERE a->>'action_type' IN ('initiate_checkout','checkout_initiated')),0)::INT AS checkout_initatied,
    COALESCE((SELECT SUM((a->>'value')::NUMERIC) FROM jsonb_array_elements(r.actions) a WHERE a->>'action_type' = 'add_payment_info'),0)::INT AS add_payment_info,
    COALESCE((SELECT SUM((a->>'value')::NUMERIC) FROM jsonb_array_elements(r.actions) a WHERE a->>'action_type' = 'purchase'),0)::INT AS number_of_purchases,
    r.spend::NUMERIC(14,2) AS spend,
    -- Use only 'purchase' action type to avoid double-counting (purchase and offsite_conversion.fb_pixel_purchase are the same conversions)
    COALESCE((SELECT SUM((v->>'value')::NUMERIC) FROM jsonb_array_elements(r.action_values) v WHERE v->>'action_type' = 'purchase'),0)::NUMERIC(14,2) AS revenue,
    (r.date_start::timestamp AT TIME ZONE 'UTC') AS created_at,
    r.objective AS campaign_level_type,
    -- Creative + product linking columns
    c.creative_id,
    COALESCE(
      c.object_story_spec->'link_data'->>'link',
      c.object_story_spec->'link_data'->'call_to_action'->'value'->>'link',
      c.object_story_spec->'template_data'->>'link',
      c.object_story_spec->'video_data'->'call_to_action'->'value'->>'link',
      c.asset_feed_spec->'link_urls'->0->>'website_url',
      c.call_to_action->'value'->>'link',
      c.post_link
    ) AS product_url,
    -- Ad destination link: COALESCE of object_story_spec link and asset_feed_spec website_url
    COALESCE(
      c.object_story_spec->'video_data'->'call_to_action'->'value'->>'link',
      c.object_story_spec->'link_data'->'call_to_action'->'value'->>'link',
      c.object_story_spec->'link_data'->>'link',
      c.object_story_spec->'template_data'->>'link',
      c.asset_feed_spec->'link_urls'->0->>'website_url',
      c.call_to_action->'value'->>'link',
      c.post_link
    ) AS link,
    p.product_id,
    p.title AS product_name,
    -- Reach & frequency (top-level Meta scalars; frequency = impressions/reach per ad-day)
    r.reach,
    r.frequency,
    -- Video engagement metrics
    r.video_plays,
    r.video_p25,
    r.video_p50,
    r.video_p75,
    r.video_p95,
    r.video_p100,
    r.video_avg_watch_time,
    r.video_30s,
    r.cost_per_thruplay,
    r.cost_per_15s_view,
    -- 3s video views from actions JSONB
    COALESCE((SELECT SUM((a->>'value')::NUMERIC) FROM jsonb_array_elements(r.actions) a WHERE a->>'action_type' = 'video_view'), 0)::BIGINT AS video_3s_views,
    -- Hook Rate = 3s video views / impressions
    CASE WHEN r.impressions > 0
      THEN COALESCE((SELECT SUM((a->>'value')::NUMERIC) FROM jsonb_array_elements(r.actions) a WHERE a->>'action_type' = 'video_view'), 0)::NUMERIC / r.impressions
      ELSE NULL END AS hook_rate,
    -- ThruPlay count = spend / cost_per_thruplay
    CASE WHEN r.cost_per_thruplay > 0 THEN (r.spend / r.cost_per_thruplay)::BIGINT ELSE NULL END AS thruplay_count,
    -- Hold Rate = thruplay_count / 3s video views
    CASE WHEN COALESCE((SELECT SUM((a->>'value')::NUMERIC) FROM jsonb_array_elements(r.actions) a WHERE a->>'action_type' = 'video_view'), 0) > 0
         AND r.cost_per_thruplay > 0
      THEN (r.spend / r.cost_per_thruplay)::NUMERIC / COALESCE((SELECT SUM((a->>'value')::NUMERIC) FROM jsonb_array_elements(r.actions) a WHERE a->>'action_type' = 'video_view'), 1)
      ELSE NULL END AS hold_rate,
    -- Ad format derived from creative object_type
    CASE
      WHEN c.object_type = 'VIDEO' THEN 'video'
      WHEN c.object_type = 'PHOTO' THEN 'image'
      WHEN c.object_type = 'SHARE' AND c.asset_feed_spec->'videos' IS NOT NULL THEN 'video'
      WHEN c.object_type = 'SHARE' AND c.object_story_spec->'link_data'->'child_attachments' IS NOT NULL THEN 'carousel'
      WHEN c.object_type = 'SHARE' THEN 'image'
      ELSE COALESCE(c.object_type, 'unknown')
    END AS ad_format,
    -- thumbnail_url: original_image_url (from /adimages hash lookup) > video_data > link_data > thumbnail
    COALESCE(
      c.original_image_url,
      c.object_story_spec->'video_data'->>'image_url',
      c.object_story_spec->'link_data'->>'picture',
      c.object_story_spec->'link_data'->'child_attachments'->0->>'picture',
      c.asset_feed_spec->'images'->0->>'url',
      c.thumbnail_url
    ) AS thumbnail_url,
    -- image_url: original_image_url > link_data > video_data thumbnail
    COALESCE(
      c.original_image_url,
      c.object_story_spec->'link_data'->>'picture',
      c.object_story_spec->'link_data'->'child_attachments'->0->>'picture',
      c.asset_feed_spec->'images'->0->>'url',
      c.image_url,
      c.object_story_spec->'video_data'->>'image_url'
    ) AS image_url,
    c.original_image_url
  FROM raw_meta_ad_insights r
  LEFT JOIN raw_meta_ad_creative c
    ON r.organization_id = c.organization_id
    AND r.ad_id = c.ad_id
  LEFT JOIN LATERAL (
    -- Extract product handle from URL: parse '/products/{handle}' segment
    -- Handles: /products/blue-widget, /products/blue-widget?utm_source=fb, /collections/all/products/blue-widget
    SELECT NULLIF(
      split_part(
        split_part(
          COALESCE(
            c.object_story_spec->'link_data'->>'link',
            c.object_story_spec->'link_data'->'call_to_action'->'value'->>'link',
            c.object_story_spec->'template_data'->>'link',
            c.object_story_spec->'video_data'->'call_to_action'->'value'->>'link',
            c.asset_feed_spec->'link_urls'->0->>'website_url',
            c.call_to_action->'value'->>'link',
            c.post_link
          ),
          '/products/', 2
        ),
        '?', 1
      ),
      ''
    ) AS extracted_handle
  ) url_parsed ON true
  LEFT JOIN products p
    ON r.organization_id = p.organization_id
    AND p.handle = url_parsed.extracted_handle
    AND url_parsed.extracted_handle IS NOT NULL
  WHERE r.organization_id = %(org)s
    AND (date_start::timestamp AT TIME ZONE 'UTC') >= %(start)s AND (date_start::timestamp AT TIME ZONE 'UTC') < %(end)s
)
INSERT INTO advertising (
  organization_id,
  source_type,
  ad_id,
  ad_name,
  ad_group_id,
  ad_group_name,
  ad_status,
  ad_group_status,
  campaign_id,
  campaign_name,
  campaign_status,
  account_id,
  account_name,
  impressions,
  clicks,
  landing_page_view,
  add_to_cart,
  checkout_initatied,
  add_payment_info,
  number_of_purchases,
  spend,
  revenue,
  created_at,
  campaign_level_type,
  creative_id,
  product_url,
  link,
  product_id,
  product_name,
  video_plays,
  video_p25,
  video_p50,
  video_p75,
  video_p95,
  video_p100,
  video_avg_watch_time,
  video_30s,
  cost_per_thruplay,
  cost_per_15s_view,
  video_3s_views,
  hook_rate,
  thruplay_count,
  hold_rate,
  ad_format,
  thumbnail_url,
  image_url,
  original_image_url,
  reach,
  frequency
)
SELECT
  organization_id,
  source_type,
  ad_id,
  ad_name,
  ad_group_id,
  ad_group_name,
  ad_status,
  ad_group_status,
  campaign_id,
  campaign_name,
  campaign_status,
  account_id,
  account_name,
  impressions,
  clicks,
  landing_page_view,
  add_to_cart,
  checkout_initatied,
  add_payment_info,
  number_of_purchases,
  spend,
  revenue,
  created_at,
  campaign_level_type,
  creative_id,
  product_url,
  link,
  product_id,
  product_name,
  video_plays,
  video_p25,
  video_p50,
  video_p75,
  video_p95,
  video_p100,
  video_avg_watch_time,
  video_30s,
  cost_per_thruplay,
  cost_per_15s_view,
  video_3s_views,
  hook_rate,
  thruplay_count,
  hold_rate,
  ad_format,
  thumbnail_url,
  image_url,
  original_image_url,
  reach,
  frequency
FROM base_ads
