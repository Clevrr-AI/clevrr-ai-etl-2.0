-- Legacy connectors/google_ads/raw_sanitized_ad_v2.sql, ported by rule only: values are bound
-- (%(org)s, %(start)s, %(end)s: the run's days) instead of pasted text, and a literal percent sign is doubled.
--;;
-- New v2 transform using category-based conversion mapping.
--
-- NO DDL IN THIS FILE — not CREATE TABLE, not CREATE INDEX, not ALTER TABLE. This transform
-- runs once per org inside a 4-way parallel pool; `ALTER TABLE … IF NOT EXISTS` /
-- `CREATE INDEX IF NOT EXISTS` take AccessExclusive/Share locks BEFORE the existence check
-- and deadlocked parallel org runs (Aug 17 + 23 2026 in this file). Schema lives in
-- ETL/migrations/2026-08-31_001_advertising_columns.sql (applied by utils/migrations_runner.py).

-- Idempotency guards: prevent duplicate rows in advertising for google_ads loads
-- These are safe to run repeatedly.
-- Purge duplicate ad-level rows
WITH dupe_ad AS (
  SELECT id
  FROM (
    SELECT id,
           ROW_NUMBER() OVER (
             PARTITION BY organization_id, ad_id, created_at
             ORDER BY id DESC
           ) AS rn
    FROM advertising
    -- Org-scoped: an unscoped scan row-locked other orgs' rows under the 4-way parallel
    -- pool; duplicates are per-org by definition (organization_id is in the PARTITION BY).
    WHERE source_type = 'google_ads' AND ad_id IS NOT NULL
      AND organization_id = %(org)s
  ) t
  WHERE rn > 1
)
DELETE FROM advertising a USING dupe_ad d WHERE a.id = d.id
--;;
-- Purge duplicate campaign-level rows
WITH dupe_campaign AS (
  SELECT id
  FROM (
    SELECT id,
           ROW_NUMBER() OVER (
             PARTITION BY organization_id, campaign_id, created_at
             ORDER BY id DESC
           ) AS rn
    FROM advertising
    WHERE source_type = 'google_ads' AND ad_id IS NULL AND campaign_id IS NOT NULL
      AND organization_id = %(org)s
  ) t
  WHERE rn > 1
)
DELETE FROM advertising a USING dupe_campaign d WHERE a.id = d.id
--;;
-- Clear existing Google Ads data for the same organization and date range to prevent duplication.
-- Falls back to raw_google_ads_campaign for orgs that only run PMax campaigns (no ad_group_ad rows).
DELETE FROM advertising
WHERE source_type = 'google_ads'
  AND organization_id = %(org)s
  AND created_at >= (
    SELECT COALESCE(
      (SELECT MIN(__hevo_report_date) FROM raw_google_ads_ad_group_ad WHERE true AND __hevo_report_date >= %(start)s AND __hevo_report_date < %(end)s AND organization_id = %(org)s),
      (SELECT MIN(__hevo_report_date) FROM raw_google_ads_campaign       WHERE true AND __hevo_report_date >= %(start)s AND __hevo_report_date < %(end)s AND organization_id = %(org)s)
    )
  )
  AND created_at <= (
    SELECT COALESCE(
      (SELECT MAX(__hevo_report_date) FROM raw_google_ads_ad_group_ad WHERE true AND __hevo_report_date >= %(start)s AND __hevo_report_date < %(end)s AND organization_id = %(org)s),
      (SELECT MAX(__hevo_report_date) FROM raw_google_ads_campaign       WHERE true AND __hevo_report_date >= %(start)s AND __hevo_report_date < %(end)s AND organization_id = %(org)s)
    )
  )
--;;
-- Align with existing advertising table structure (no new column introduction):
-- id (PK, serial/uuid) not provided here; last_synced handled by default; we populate existing metrics only.
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
  link,
  video_views,
  video_quartile_p25_rate,
  video_quartile_p50_rate,
  video_quartile_p75_rate,
  video_quartile_p100_rate,
  video_view_rate,
  hook_rate,
  hold_rate,
  ad_format
)
WITH
base_ads AS (
  SELECT
    a.organization_id,
    a.customer_id                        AS account_id,
    a.campaign_id::BIGINT                AS campaign_id,
    a.campaign_name,
    a.campaign_status,
    a.advertising_channel_type,
    a.advertising_channel_sub_type,
    a.ad_group_id::BIGINT                AS ad_group_id,
    a.ad_group_name,
    a.ad_group_status,
    a.ad_id::BIGINT                      AS ad_id,
    a.ad_name,
    a.ad_status,
    a.impressions,
    a.clicks,
    a.cost_micros,
    a.conversions,
    a.conversions_value,
    a.video_views,
    a.video_quartile_p25_rate,
    a.video_quartile_p50_rate,
    a.video_quartile_p75_rate,
    a.video_quartile_p100_rate,
    a.video_view_rate,
    CAST(a.__hevo_report_date AS TIMESTAMP) AS created_at
  FROM raw_google_ads_ad_group_ad a
  WHERE true AND __hevo_report_date >= %(start)s AND __hevo_report_date < %(end)s AND organization_id = %(org)s
),
ag_clean AS (
  SELECT
    id::BIGINT AS ad_group_id,
    name       AS ad_group_name,
    status     AS ad_group_status,
    CAST(__hevo_report_date AS TIMESTAMP) AS created_at,
    campaign
  FROM raw_google_ads_ad_group
  WHERE true AND __hevo_report_date >= %(start)s AND __hevo_report_date < %(end)s AND organization_id = %(org)s
),
campaign_clean AS (
  SELECT
    id::BIGINT                           AS campaign_id,
    organization_id,
    name                                 AS campaign_name,
    status                               AS campaign_status,
    customer_id                          AS account_id,
    advertising_channel_type,
    advertising_channel_sub_type,
    impressions,
    clicks,
    cost_micros,
    revenue_micros,
    CAST(__hevo_report_date AS TIMESTAMP) AS created_at
  FROM raw_google_ads_campaign
  WHERE true AND __hevo_report_date >= %(start)s AND __hevo_report_date < %(end)s AND organization_id = %(org)s
),
-- ONE TRACKER PER STEP (2026-09-29)
-- A brand can run several conversion actions ("trackers") that count the SAME event.
-- thedeconstruct.in has three purchase trackers (Google tag, Shopify's Google & YouTube app,
-- GA4 import) all counting the same orders. Summing them inflated purchases ~2x, while revenue
-- used Google's PRIMARY conversions_value only — ₹0 whenever the primary tracker is a late GA4
-- import (Sep 21-24 2026) — so panels showed "50 purchases, ₹0 sales".
-- Rule, per ad (conv) or per campaign (campaign_conv), per day. Trackers are never summed:
--   * each action is classified into exactly ONE step (purchase takes precedence);
--   * PURCHASE: the tracker with the most conversions wins; number_of_purchases AND revenue
--     both come from it (ties: higher value, then resource name, so reruns are deterministic).
--     No purchase tracker converted -> revenue falls back to Google's primary
--     conversions_value (see ad_lv / campaign_lv).
--   * FUNNEL (LPV / ATC / checkout / payment): ALWAYS from Shopify's Google & YouTube app
--     ("Google Shopping App …" trackers; LPV = its "Page View" only, not View Item / Search).
--     No fallback to other trackers (product decision 2026-09-29): if the app has no data for
--     a step, that step is 0. The Google tag and GA4 send purchases only, so the funnel cannot
--     follow the purchase winner.
-- raw_*_conv_action_perf.conversions / conversions_value hold all_conversions /
-- all_conversions_value (google_ads_fetcher.py), so secondary trackers are included.
action_step AS (
  SELECT
    x.resource_name,
    x.status,
    x.step,
    CASE
      WHEN x.step = 'purchase'          THEN TRUE
      WHEN x.step = 'landing_page_view' THEN x.lname LIKE 'google shopping app page view%%'
      ELSE x.lname LIKE 'google shopping app%%'
    END AS is_preferred
  FROM (
    SELECT
      a.resource_name,
      a.status,
      LOWER(COALESCE(a.name, '')) AS lname,
      CASE
        WHEN a.category = 'PURCHASE'
          OR LOWER(COALESCE(a.name, '')) LIKE '%%purchase%%'
        THEN 'purchase'
        WHEN a.category = 'ADD_PAYMENT_INFO'
          OR LOWER(COALESCE(a.name, '')) LIKE '%%add payment%%'
          OR LOWER(COALESCE(a.name, '')) LIKE '%%payment info%%'
        THEN 'add_payment_info'
        WHEN a.category = 'BEGIN_CHECKOUT'
          OR LOWER(COALESCE(a.name, '')) LIKE '%%begin checkout%%'
          OR LOWER(COALESCE(a.name, '')) LIKE '%%initiate checkout%%'
          OR LOWER(COALESCE(a.name, '')) LIKE '%%checkout%%'
        THEN 'checkout_initiated'
        WHEN a.category = 'ADD_TO_CART'
          OR LOWER(COALESCE(a.name, '')) LIKE '%%add to cart%%'
          OR LOWER(COALESCE(a.name, '')) LIKE '%%add_to_cart%%'
        THEN 'add_to_cart'
        WHEN a.category = 'PAGE_VIEW'
          OR LOWER(COALESCE(a.name, '')) LIKE '%%landing page view%%'
          OR LOWER(COALESCE(a.name, '')) LIKE '%%page view%%'
        THEN 'landing_page_view'
      END AS step
    FROM raw_google_ads_conversion_action a
  ) x
),
conv_action AS (
  SELECT
    p.ad_id::BIGINT                        AS ad_id,
    CAST(p.__hevo_report_date AS TIMESTAMP) AS created_at,
    p.conversion_action,
    ast.step,
    ast.is_preferred,
    SUM(p.conversions)                      AS conversions,
    SUM(p.conversions_value)                AS conversions_value
  FROM raw_google_ads_conv_action_perf p
  JOIN action_step ast
    ON ast.resource_name = p.conversion_action
  WHERE true AND __hevo_report_date >= %(start)s AND __hevo_report_date < %(end)s
    AND p.organization_id = %(org)s
    AND ast.step IS NOT NULL
  GROUP BY 1, 2, 3, 4, 5
),
conv_winner AS (
  SELECT *
  FROM (
    SELECT
      ca.*,
      ROW_NUMBER() OVER (
        PARTITION BY ca.ad_id, ca.created_at, ca.step
        ORDER BY COALESCE(ca.conversions, 0) DESC,
                 COALESCE(ca.conversions_value, 0) DESC,
                 ca.conversion_action
      ) AS rn
    FROM conv_action ca
    -- purchase: every tracker competes (is_preferred is always TRUE). Funnel: app tracker only.
    WHERE ca.is_preferred
  ) t
  WHERE rn = 1
),
conv AS (
  SELECT
    ad_id,
    created_at,
    MAX(CASE WHEN step = 'landing_page_view'  THEN conversions END)       AS landing_page_view,
    MAX(CASE WHEN step = 'add_to_cart'        THEN conversions END)       AS add_to_cart,
    MAX(CASE WHEN step = 'checkout_initiated' THEN conversions END)       AS checkout_initiated,
    MAX(CASE WHEN step = 'add_payment_info'   THEN conversions END)       AS add_payment_info,
    MAX(CASE WHEN step = 'purchase'           THEN conversions END)       AS number_of_purchases,
    MAX(CASE WHEN step = 'purchase'           THEN conversions_value END) AS purchase_value
  FROM conv_winner
  GROUP BY ad_id, created_at
),
campaign_conv_action AS (
  SELECT
    p.campaign_id::BIGINT                  AS campaign_id,
    CAST(p.__hevo_report_date AS TIMESTAMP) AS created_at,
    p.conversion_action,
    ast.step,
    ast.is_preferred,
    SUM(p.conversions)                      AS conversions,
    SUM(p.conversions_value)                AS conversions_value
  FROM raw_google_ads_campaign_conv_action_perf p
  JOIN action_step ast
    ON ast.resource_name = p.conversion_action
  WHERE true AND __hevo_report_date >= %(start)s AND __hevo_report_date < %(end)s
    AND p.organization_id = %(org)s
    AND ast.step IS NOT NULL
  GROUP BY 1, 2, 3, 4, 5
),
campaign_conv_winner AS (
  SELECT *
  FROM (
    SELECT
      ca.*,
      ROW_NUMBER() OVER (
        PARTITION BY ca.campaign_id, ca.created_at, ca.step
        ORDER BY COALESCE(ca.conversions, 0) DESC,
                 COALESCE(ca.conversions_value, 0) DESC,
                 ca.conversion_action
      ) AS rn
    FROM campaign_conv_action ca
    -- purchase: every tracker competes (is_preferred is always TRUE). Funnel: app tracker only.
    WHERE ca.is_preferred
  ) t
  WHERE rn = 1
),
campaign_conv AS (
  SELECT
    campaign_id,
    created_at,
    MAX(CASE WHEN step = 'landing_page_view'  THEN conversions END)       AS landing_page_view,
    MAX(CASE WHEN step = 'add_to_cart'        THEN conversions END)       AS add_to_cart,
    MAX(CASE WHEN step = 'checkout_initiated' THEN conversions END)       AS checkout_initiated,
    MAX(CASE WHEN step = 'add_payment_info'   THEN conversions END)       AS add_payment_info,
    MAX(CASE WHEN step = 'purchase'           THEN conversions END)       AS number_of_purchases,
    MAX(CASE WHEN step = 'purchase'           THEN conversions_value END) AS purchase_value
  FROM campaign_conv_winner
  GROUP BY campaign_id, created_at
),
campaigns_with_ads AS (
  SELECT DISTINCT campaign_id, created_at
  FROM base_ads
  WHERE ad_id IS NOT NULL
),
ad_lv AS (
  SELECT
    ba.organization_id,
    'google_ads'                          AS source_type,
    ba.ad_id,
    -- Google only exposes ad.name for legacy upload formats (image/video); Search,
    -- Shopping and Demand Gen ads come back with it blank. Ad-level dashboards group
    -- and filter on ad_name, and the metric layer drops blank-keyed groups outright
    -- (app/utils/utility.py remove_empty_or_null_groups), so a blank name makes the
    -- ad — and its spend — invisible. Synthesize a unique, human-readable label:
    -- campaign · ad group · ad_id
    COALESCE(
      NULLIF(ba.ad_name, ''),
      CONCAT_WS(' · ',
        NULLIF(COALESCE(ba.campaign_name, cc.campaign_name), ''),
        NULLIF(COALESCE(ba.ad_group_name, ag.ad_group_name), ''),
        ba.ad_id::TEXT
      )
    )                                     AS ad_name,
    ba.ad_group_id,
    COALESCE(ba.ad_group_name, ag.ad_group_name) AS ad_group_name,
    ba.ad_status,
    COALESCE(ba.ad_group_status, ag.ad_group_status) AS ad_group_status,
    ba.campaign_id,
    COALESCE(ba.campaign_name, cc.campaign_name)     AS campaign_name,
    COALESCE(ba.campaign_status, cc.campaign_status) AS campaign_status,
  ba.account_id,
  NULL::TEXT                            AS account_name,
    ba.impressions,
    ba.clicks,
  ROUND(COALESCE(conv.landing_page_view, 0))::INT   AS landing_page_view,
    ROUND(COALESCE(conv.add_to_cart, 0))::INT         AS add_to_cart,
    ROUND(COALESCE(conv.checkout_initiated, 0))::INT  AS checkout_initatied,
    ROUND(COALESCE(conv.add_payment_info, 0))::INT    AS add_payment_info,
    ROUND(COALESCE(conv.number_of_purchases, 0))::INT AS number_of_purchases,
    (COALESCE(ba.cost_micros, 0) / 1e6)::NUMERIC(14,2)   AS spend,
    -- Revenue comes from the same tracker as number_of_purchases; Google's primary value
    -- only when no purchase tracker converted (see ONE TRACKER PER STEP above).
    (CASE WHEN COALESCE(conv.number_of_purchases, 0) > 0
          THEN COALESCE(conv.purchase_value, 0)
          ELSE COALESCE(ba.conversions_value, 0)
     END)::NUMERIC(14,2)                                 AS revenue,
    ba.created_at,
    COALESCE(ba.advertising_channel_type, cc.advertising_channel_type)::TEXT AS campaign_level_type,
    NULL::TEXT AS link,
    ba.video_views,
    ba.video_quartile_p25_rate,
    ba.video_quartile_p50_rate,
    ba.video_quartile_p75_rate,
    ba.video_quartile_p100_rate,
    ba.video_view_rate,
    -- Google Hook Rate ~ video_quartile_p25_rate
    ba.video_quartile_p25_rate::NUMERIC(10,6) AS hook_rate,
    -- Google Hold Rate = p75 / p25
    CASE WHEN ba.video_quartile_p25_rate > 0
      THEN (ba.video_quartile_p75_rate / ba.video_quartile_p25_rate)::NUMERIC(10,6)
      ELSE NULL END AS hold_rate,
    -- Ad format from creative snapshot
    CASE
      WHEN gc.ad_type = 'VIDEO_AD' THEN 'video'
      WHEN gc.ad_type = 'IMAGE_AD' THEN 'image'
      WHEN gc.ad_type = 'RESPONSIVE_DISPLAY_AD' THEN 'responsive_display'
      WHEN gc.ad_type = 'RESPONSIVE_SEARCH_AD' THEN 'responsive_search'
      WHEN gc.ad_type IS NOT NULL THEN LOWER(gc.ad_type)
      ELSE 'unknown'
    END AS ad_format
  FROM base_ads ba
  LEFT JOIN ag_clean ag
    ON ag.ad_group_id = ba.ad_group_id
   AND ag.created_at  = ba.created_at
  LEFT JOIN campaign_clean cc
    ON cc.campaign_id = ba.campaign_id
   AND cc.created_at  = ba.created_at
  LEFT JOIN conv
    ON conv.ad_id     = ba.ad_id
   AND conv.created_at = ba.created_at
  LEFT JOIN raw_google_ads_ad_creative gc
    ON gc.ad_id = ba.ad_id
   AND gc.organization_id = ba.organization_id
  WHERE ba.ad_id IS NOT NULL
),
campaign_lv AS (
  SELECT
  cc.organization_id,
    'google_ads'                         AS source_type,
    NULL::BIGINT                         AS ad_id,
    NULL::TEXT                           AS ad_name,
    NULL::BIGINT                         AS ad_group_id,
    NULL::TEXT                           AS ad_group_name,
    NULL::TEXT                           AS ad_status,
    NULL::TEXT                           AS ad_group_status,
    cc.campaign_id,
    cc.campaign_name,
    cc.campaign_status,
  cc.account_id,
  NULL::TEXT                           AS account_name,
    cc.impressions,
    cc.clicks,
    ROUND(COALESCE(ccap.landing_page_view, 0))::INT   AS landing_page_view,
    ROUND(COALESCE(ccap.add_to_cart, 0))::INT         AS add_to_cart,
    ROUND(COALESCE(ccap.checkout_initiated, 0))::INT  AS checkout_initatied,
    ROUND(COALESCE(ccap.add_payment_info, 0))::INT    AS add_payment_info,
    ROUND(COALESCE(ccap.number_of_purchases, 0))::INT AS number_of_purchases,
    (COALESCE(cc.cost_micros, 0) / 1e6)::NUMERIC(14,2)  AS spend,
    (CASE WHEN COALESCE(ccap.number_of_purchases, 0) > 0
          THEN COALESCE(ccap.purchase_value, 0)
          ELSE COALESCE(cc.revenue_micros, 0) / 1e6
     END)::NUMERIC(14,2)                                 AS revenue,
    cc.created_at,
    cc.advertising_channel_type::TEXT AS campaign_level_type,
    NULL::TEXT AS link,
    NULL::BIGINT AS video_views,
    NULL::DOUBLE PRECISION AS video_quartile_p25_rate,
    NULL::DOUBLE PRECISION AS video_quartile_p50_rate,
    NULL::DOUBLE PRECISION AS video_quartile_p75_rate,
    NULL::DOUBLE PRECISION AS video_quartile_p100_rate,
    NULL::DOUBLE PRECISION AS video_view_rate,
    NULL::NUMERIC(10,6) AS hook_rate,
    NULL::NUMERIC(10,6) AS hold_rate,
    NULL::TEXT AS ad_format
  FROM campaign_clean cc
  LEFT JOIN campaign_conv ccap
    ON ccap.campaign_id = cc.campaign_id
   AND ccap.created_at  = cc.created_at
  WHERE cc.campaign_status != 'PAUSED'
    AND NOT EXISTS (
      SELECT 1 FROM campaigns_with_ads ca
      WHERE ca.campaign_id = cc.campaign_id
        AND ca.created_at  = cc.created_at
    )
),
ad_lv_dedup AS (
  SELECT *
  FROM (
    SELECT
      al.*,
      ROW_NUMBER() OVER (
        PARTITION BY al.organization_id, al.ad_id, al.created_at
        ORDER BY COALESCE(al.impressions, 0) DESC, COALESCE(al.clicks, 0) DESC
      ) AS rn
    FROM ad_lv al
  ) t
  WHERE rn = 1
),
campaign_lv_dedup AS (
  SELECT *
  FROM (
    SELECT
      cl.*,
      ROW_NUMBER() OVER (
        PARTITION BY cl.organization_id, cl.campaign_id, cl.created_at
        ORDER BY COALESCE(cl.impressions, 0) DESC, COALESCE(cl.clicks, 0) DESC
      ) AS rn
    FROM campaign_lv cl
    WHERE cl.campaign_id IS NOT NULL
  ) t
  WHERE rn = 1
)
SELECT
  organization_id, source_type, ad_id, ad_name, ad_group_id, ad_group_name, ad_status, ad_group_status,
  campaign_id, campaign_name, campaign_status, account_id, account_name, impressions, clicks,
  landing_page_view, add_to_cart, checkout_initatied, add_payment_info, number_of_purchases,
  spend, revenue, created_at, campaign_level_type, link,
  video_views, video_quartile_p25_rate, video_quartile_p50_rate, video_quartile_p75_rate,
  video_quartile_p100_rate, video_view_rate, hook_rate, hold_rate, ad_format
FROM ad_lv_dedup
UNION ALL
SELECT
  organization_id, source_type, ad_id, ad_name, ad_group_id, ad_group_name, ad_status, ad_group_status,
  campaign_id, campaign_name, campaign_status, account_id, account_name, impressions, clicks,
  landing_page_view, add_to_cart, checkout_initatied, add_payment_info, number_of_purchases,
  spend, revenue, created_at, campaign_level_type, link,
  video_views, video_quartile_p25_rate, video_quartile_p50_rate, video_quartile_p75_rate,
  video_quartile_p100_rate, video_view_rate, hook_rate, hold_rate, ad_format
FROM campaign_lv_dedup
