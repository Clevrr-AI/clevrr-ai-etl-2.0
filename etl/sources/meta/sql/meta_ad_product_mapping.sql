-- Legacy connectors/meta_ads/raw_sanitized_ad_product_mapping.sql, ported by rule only: values are bound
-- (%(org)s) instead of pasted text, and a literal percent sign is doubled.
--;;
-- Build ad-to-product mappings from Meta Ads creative URLs.
-- Seven link paths are tried in order: the five object_story_spec / asset_feed_spec fields,
-- the creative's top-level call_to_action, then post_link — the call_to_action of the Page
-- post an existing-post creative was built from, read with the Page token (the only place
-- the link exists when the creative itself carries none).
-- Handles two URL patterns:
--   1. /products/{handle}       → direct product mapping (link_type = 'product')
--   2. /collections/{handle}    → fan-out to all products in that collection (link_type = 'collection')
-- URLs containing both /collections/ and /products/ are treated as direct product mappings.

-- NO DDL IN THIS FILE — the source_type column, its one-time backfill and idx_apm_org_source
-- moved to ETL/migrations/2026-08-31_004_ad_product_mapping.sql (applied by
-- utils/migrations_runner.py). The self-migration here ran per org per night under a 4-way
-- parallel pool and its locks deadlocked parallel org runs.

-- Full refresh per org, scoped to this platform so the google_ads transform's rows survive.
-- Creatives are not time-series data, so date windowing is not applicable.
DELETE FROM ad_product_mapping
WHERE organization_id = %(org)s
  AND source_type = 'meta_ads'
--;;
WITH url_extraction AS (
  SELECT
    c.organization_id,
    c.ad_id::BIGINT AS ad_id,
    COALESCE(
      c.object_story_spec->'video_data'->'call_to_action'->'value'->>'link',
      c.object_story_spec->'link_data'->'call_to_action'->'value'->>'link',
      c.object_story_spec->'link_data'->>'link',
      c.object_story_spec->'template_data'->>'link',
      c.asset_feed_spec->'link_urls'->0->>'website_url',
      c.call_to_action->'value'->>'link',
      c.post_link
    ) AS url
  FROM raw_meta_ad_creative c
  WHERE c.organization_id = %(org)s
    AND c.ad_id IS NOT NULL
),

-- Direct product mappings: URL contains /products/
product_urls AS (
  SELECT
    ue.organization_id,
    ue.ad_id,
    NULLIF(
      split_part(split_part(ue.url, '/products/', 2), '?', 1),
      ''
    ) AS product_handle
  FROM url_extraction ue
  WHERE ue.url LIKE '%%/products/%%'
),

product_mappings AS (
  SELECT
    pu.organization_id,
    pu.ad_id,
    p.product_id,
    p.title AS product_name,
    'product'::VARCHAR(32) AS link_type,
    NULL::TEXT AS collection_handle
  FROM product_urls pu
  JOIN products p
    ON p.organization_id = pu.organization_id
    AND p.handle = pu.product_handle
  WHERE pu.product_handle IS NOT NULL
),

-- Collection fan-out mappings: URL contains /collections/ but NOT /products/
collection_urls AS (
  SELECT
    ue.organization_id,
    ue.ad_id,
    NULLIF(
      split_part(
        split_part(
          split_part(ue.url, '/collections/', 2),
          '/', 1
        ),
        '?', 1
      ),
      ''
    ) AS collection_handle
  FROM url_extraction ue
  WHERE ue.url LIKE '%%/collections/%%'
    AND ue.url NOT LIKE '%%/products/%%'
),

collection_mappings AS (
  SELECT
    cu.organization_id,
    cu.ad_id,
    p.product_id,
    p.title AS product_name,
    'collection'::VARCHAR(32) AS link_type,
    cu.collection_handle
  FROM collection_urls cu
  JOIN raw_shopify_collections col
    ON col.organization_id = cu.organization_id
    AND col.handle = cu.collection_handle
  JOIN raw_shopify_collects rc
    ON rc.organization_id = col.organization_id
    AND rc.collection_id = col.collection_id
  JOIN products p
    ON p.organization_id = rc.organization_id
    AND p.product_id = rc.product_id
  WHERE cu.collection_handle IS NOT NULL
    AND cu.collection_handle != 'all'
)

INSERT INTO ad_product_mapping (
  organization_id,
  ad_id,
  product_id,
  product_name,
  link_type,
  collection_handle,
  source_type
)
SELECT *, 'meta_ads'::VARCHAR(32) FROM product_mappings
UNION ALL
SELECT *, 'meta_ads'::VARCHAR(32) FROM collection_mappings
ON CONFLICT (organization_id, ad_id, product_id, source_type)
DO UPDATE SET
  product_name = EXCLUDED.product_name,
  link_type = EXCLUDED.link_type,
  collection_handle = EXCLUDED.collection_handle
