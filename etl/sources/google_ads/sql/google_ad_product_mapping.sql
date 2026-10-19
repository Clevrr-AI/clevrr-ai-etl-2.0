-- Legacy connectors/google_ads/raw_sanitized_ad_product_mapping.sql, ported by rule only: values are bound
-- (%(org)s) instead of pasted text, and a literal percent sign is doubled.
--;;
-- Build ad-to-product mappings from Google Ads final URLs.
--
-- Google's analogue of connectors/meta_ads/raw_sanitized_ad_product_mapping.sql. The
-- destination table, link_type vocabulary and collection fan-out are identical; only the
-- URL source differs: Google exposes landing pages as `ad_group_ad.ad.final_urls`, already
-- captured into raw_google_ads_ad_creative.final_urls (a JSONB array) by
-- ETL/load_google_ads_raw.py.
--
-- Handles two URL patterns:
--   1. /products/{handle}       → direct product mapping (link_type = 'product')
--   2. /collections/{handle}    → fan-out to all products in that collection (link_type = 'collection')
-- URLs containing both /collections/ and /products/ are treated as direct product mappings.
--
-- COVERAGE LIMIT: this only reaches campaigns that have ad_group_ad entities — Search,
-- Display, Demand Gen, Video. Performance Max and Shopping campaigns have no ads (see the
-- note at ETL/fetchers/google_ads_fetcher.py), so they produce no creative rows and no
-- mappings here. Product-level attribution for those comes from
-- connectors/google_ads/raw_sanitized_google_ads_product_perf.sql instead.
--

-- NO DDL IN THIS FILE — the source_type column, its one-time backfill and idx_apm_org_source
-- moved to ETL/migrations/2026-08-31_004_ad_product_mapping.sql (applied by
-- utils/migrations_runner.py). The self-migration here ran per org per night under a 4-way
-- parallel pool and its locks deadlocked parallel org runs.

-- Full refresh per org, scoped to this platform so the meta_ads transform's rows survive.
-- Creatives are not time-series data, so date windowing is not applicable.
DELETE FROM ad_product_mapping
WHERE organization_id = %(org)s
  AND source_type = 'google_ads'
--;;
WITH url_extraction AS (
  -- An ad can carry several final URLs; unnest so each one gets a chance to resolve.
  -- Non-array / NULL final_urls are skipped rather than erroring.
  SELECT DISTINCT
    c.organization_id,
    c.ad_id::BIGINT AS ad_id,
    u.url
  FROM raw_google_ads_ad_creative c
  CROSS JOIN LATERAL jsonb_array_elements_text(c.final_urls) AS u(url)
  WHERE c.organization_id = %(org)s
    AND c.ad_id IS NOT NULL
    AND c.final_urls IS NOT NULL
    AND jsonb_typeof(c.final_urls) = 'array'
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
  SELECT DISTINCT
    pu.organization_id,
    pu.ad_id,
    p.product_id,
    p.title AS product_name,
    'product'::VARCHAR(32) AS link_type,
    NULL::TEXT AS collection_handle,
    'google_ads'::VARCHAR(32) AS source_type
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
  SELECT DISTINCT
    cu.organization_id,
    cu.ad_id,
    p.product_id,
    p.title AS product_name,
    'collection'::VARCHAR(32) AS link_type,
    cu.collection_handle,
    'google_ads'::VARCHAR(32) AS source_type
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
),

-- A single ad may hit both branches across its several final URLs. Direct product links
-- are more specific than a collection fan-out, so they win on collision.
combined AS (
  SELECT *, 1 AS precedence FROM product_mappings
  UNION ALL
  SELECT *, 2 AS precedence FROM collection_mappings
),

deduped AS (
  SELECT DISTINCT ON (organization_id, ad_id, product_id)
    organization_id, ad_id, product_id, product_name, link_type, collection_handle, source_type
  FROM combined
  ORDER BY organization_id, ad_id, product_id, precedence
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
SELECT
  organization_id, ad_id, product_id, product_name, link_type, collection_handle, source_type
FROM deduped
ON CONFLICT (organization_id, ad_id, product_id, source_type)
DO UPDATE SET
  product_name = EXCLUDED.product_name,
  link_type = EXCLUDED.link_type,
  collection_handle = EXCLUDED.collection_handle
