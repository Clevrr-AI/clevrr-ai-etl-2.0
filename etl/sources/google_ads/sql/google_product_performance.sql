-- Legacy connectors/google_ads/raw_sanitized_google_ads_product_perf.sql, ported by rule only: values are bound
-- (%(org)s, %(start)s, %(end)s: the run's days) instead of pasted text, and a literal percent sign is doubled.
--;;
-- Per-product Google Ads spend, unioned across Shopping and Performance Max.
--
-- WHY THIS EXISTS
-- The google_ads pipeline's finest grain was ad_group_ad x day, with a campaign x day
-- fallback for Performance Max (which has no ad_group_ad entities at all — see
-- ETL/fetchers/google_ads_fetcher.py). That meant PMax and Shopping spend could only ever be
-- reported as a whole-campaign total, and `advertising.product_id` stayed NULL for every
-- google_ads row. These two views are the only per-product spend Google exposes.
--
-- HOW THIS DIFFERS FROM ad_product_mapping
-- connectors/google_ads/raw_sanitized_ad_product_mapping.sql answers "which products does
-- this ad's landing page point at" — a static creative-side link, and only for campaign
-- types that have ads. This answers "what did Google actually charge per product per day",
-- which is a metric, not a mapping, and is the only product signal that exists for PMax.
--
--
-- NO DDL IN THIS FILE — the google_ads_product_performance table and its indexes are
-- created by ETL/migrations/2026-08-31_002_raw_google_ads.sql (applied by
-- utils/migrations_runner.py). The CREATE TABLE / CREATE INDEX that used to live here ran
-- per org per night inside a 4-way parallel pool, and CREATE INDEX IF NOT EXISTS takes a
-- ShareLock even when the index already exists — a nightly deadlock source.

-- substituted from a single registry `date_column` ("p.__hevo_report_date"), and the same
-- string is injected into both WHERE clauses — so the two aliases must match. Each SELECT
-- has its own scope, so reusing `p` is legal.
WITH raw_perf AS (
  SELECT
    p.organization_id, p.customer_id, p.campaign_id, p.campaign_name,
    p.advertising_channel_type,
    'shopping'::VARCHAR(32) AS entity_level,
    p.ad_group_id   AS entity_id,
    p.ad_group_name AS entity_name,
    p.product_item_id, p.product_title,
    p.impressions, p.clicks, p.cost_micros, p.conversions, p.conversions_value,
    p.__hevo_report_date
  FROM raw_google_ads_shopping_perf p
  WHERE p.organization_id = %(org)s
    AND p.campaign_id IS NOT NULL
    -- PMax products come only from the branch below; the fetcher also excludes PMax from
    -- the Shopping query, this guards raw rows written before that filter existed.
    AND COALESCE(p.advertising_channel_type, '') <> 'PERFORMANCE_MAX'
    AND NULLIF(btrim(p.product_item_id), '') IS NOT NULL
    AND p.__hevo_report_date >= %(start)s AND p.__hevo_report_date < %(end)s

  UNION ALL

  SELECT
    p.organization_id, p.customer_id, p.campaign_id, p.campaign_name,
    p.advertising_channel_type,
    'performance_max'::VARCHAR(32) AS entity_level,
    p.asset_group_id   AS entity_id,
    p.asset_group_name AS entity_name,
    p.product_item_id, p.product_title,
    p.impressions, p.clicks, p.cost_micros, p.conversions, p.conversions_value,
    p.__hevo_report_date
  FROM raw_google_ads_asset_group_product_perf p
  WHERE p.organization_id = %(org)s
    AND p.campaign_id IS NOT NULL
    AND NULLIF(btrim(p.product_item_id), '') IS NOT NULL
    AND p.__hevo_report_date >= %(start)s AND p.__hevo_report_date < %(end)s
),

-- The same (campaign, entity, product, day) can appear on more than one raw row when a
-- backfill and a daily run both wrote it under different job_ids. Collapse first so the
-- upsert below sees one row per PK.
deduped AS (
  SELECT
    organization_id,
    MAX(customer_id) AS customer_id,
    campaign_id,
    MAX(campaign_name) AS campaign_name,
    MAX(advertising_channel_type) AS advertising_channel_type,
    entity_level,
    COALESCE(entity_id, 0) AS entity_id,
    MAX(entity_name) AS entity_name,
    product_item_id,
    MAX(product_title) AS product_title,
    __hevo_report_date,
    MAX(impressions) AS impressions,
    MAX(clicks) AS clicks,
    MAX(cost_micros) AS cost_micros,
    MAX(conversions) AS conversions,
    MAX(conversions_value) AS conversions_value
  FROM raw_perf
  GROUP BY organization_id, campaign_id, entity_level, COALESCE(entity_id, 0),
           product_item_id, __hevo_report_date
),

-- Merchant Center offer id -> our catalog.
--
-- Shopify's Google & YouTube channel emits shopify_{COUNTRY}_{product_id}_{variant_id}, but
-- merchants routinely override the id to their SKU, and other feed tools use their own
-- shapes. So parse rather than assume, and record which rule matched so the unresolved rate
-- is measurable per org instead of silently swallowed.
parsed AS (
  SELECT
    d.*,
    -- Trailing _{digits}_{digits} of a shopify_* style id: product_id then variant_id.
    -- Every numeric group is bounded to 18 digits — the widest value guaranteed to fit BIGINT
    -- (999999999999999999 < 9223372036854775807). The casts exist only to look the id up in our
    -- catalog, whose keys (product_variants.variant_id, products.product_id) are BIGINT, and
    -- Shopify ids are ~14 digits — so a wider id cannot match one anyway. An unbounded cast
    -- instead raises NumericValueOutOfRange and aborts the whole connector run: a merchant
    -- using 20-digit offer ids (shapercult.com, 2026-08-02) killed the transform, which also
    -- skipped the psql_to_clickhouse phase for advertising.
    (regexp_match(d.product_item_id, '(?i)^shopify_[a-z]{2}_([0-9]{1,18})_([0-9]{1,18})$'))[1]::BIGINT AS pat_product_id,
    (regexp_match(d.product_item_id, '(?i)^shopify_[a-z]{2}_([0-9]{1,18})_([0-9]{1,18})$'))[2]::BIGINT AS pat_variant_id,
    -- Bare numeric id: Shopify variant id used directly as the offer id. Same bound.
    CASE WHEN d.product_item_id ~ '^[0-9]{1,18}$' THEN d.product_item_id::BIGINT END AS bare_numeric_id
  FROM deduped d
),

resolved AS (
  SELECT
    p.*,
    -- Priority: the parsed shopify_ pattern's variant, then a bare numeric variant id, then
    -- the raw id treated as a SKU (the join amazon_ads already uses in its raw_sanitized_ad).
    COALESCE(pv_pat.variant_id, pv_num.variant_id, pv_sku.variant_id) AS r_variant_id,
    COALESCE(pv_pat.product_id, pv_num.product_id, pv_sku.product_id) AS r_product_id,
    CASE
      WHEN pv_pat.variant_id IS NOT NULL THEN 'shopify_pattern'
      WHEN pv_num.variant_id IS NOT NULL THEN 'variant_id'
      WHEN pv_sku.variant_id IS NOT NULL THEN 'sku'
      ELSE 'unresolved'
    END::VARCHAR(32) AS resolution_method
  FROM parsed p
  LEFT JOIN product_variants pv_pat
    ON pv_pat.organization_id = p.organization_id
    AND pv_pat.variant_id = p.pat_variant_id
  LEFT JOIN product_variants pv_num
    ON pv_num.organization_id = p.organization_id
    AND pv_num.variant_id = p.bare_numeric_id
  LEFT JOIN product_variants pv_sku
    ON pv_sku.organization_id = p.organization_id
    AND pv_sku.sku = p.product_item_id
)

INSERT INTO google_ads_product_performance (
  organization_id, source_type, report_date, customer_id,
  campaign_id, campaign_name, campaign_level_type,
  entity_level, entity_id, entity_name,
  product_item_id, product_title,
  product_id, variant_id, product_name, resolution_method,
  impressions, clicks, spend, conversions, revenue
)
SELECT
  r.organization_id,
  'google_ads'::VARCHAR(100) AS source_type,
  r.__hevo_report_date AS report_date,
  r.customer_id,
  r.campaign_id,
  r.campaign_name,
  r.advertising_channel_type AS campaign_level_type,
  r.entity_level,
  r.entity_id,
  r.entity_name,
  r.product_item_id,
  r.product_title,
  r.r_product_id AS product_id,
  r.r_variant_id AS variant_id,
  -- Prefer our own catalog title; fall back to the title Google reported so an unresolved
  -- row is still human-readable.
  COALESCE(pr.title, r.product_title) AS product_name,
  r.resolution_method,
  r.impressions,
  r.clicks,
  (COALESCE(r.cost_micros, 0) / 1000000.0)::NUMERIC(18,6) AS spend,
  r.conversions,
  COALESCE(r.conversions_value, 0)::NUMERIC(18,6) AS revenue
FROM resolved r
LEFT JOIN products pr
  ON pr.organization_id = r.organization_id
  AND pr.product_id = r.r_product_id
ON CONFLICT (organization_id, report_date, campaign_id, entity_level, entity_id, product_item_id)
DO UPDATE SET
  customer_id         = EXCLUDED.customer_id,
  campaign_name       = EXCLUDED.campaign_name,
  campaign_level_type = EXCLUDED.campaign_level_type,
  entity_name         = EXCLUDED.entity_name,
  product_title       = EXCLUDED.product_title,
  product_id          = EXCLUDED.product_id,
  variant_id          = EXCLUDED.variant_id,
  product_name        = EXCLUDED.product_name,
  resolution_method   = EXCLUDED.resolution_method,
  impressions         = EXCLUDED.impressions,
  clicks              = EXCLUDED.clicks,
  spend               = EXCLUDED.spend,
  conversions         = EXCLUDED.conversions,
  revenue             = EXCLUDED.revenue
