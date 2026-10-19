-- ad_product_mapping for one org (legacy connectors/shopify/raw_sanitized_ad_product_mapping_orders.sql).
-- Ported by rule: values are bound (%(org)s, %(exclude_pos)s, %(since)s,
-- %(created_from)s, %(created_to)s) instead of pasted ${...} text, a literal percent sign
-- is doubled, and the app's tables are read from public.
-- The window: orders updated since %(since)s (an incremental run) or created in
-- [%(created_from)s, %(created_to)s) (a backfill); legacy ran each file twice, on created_at
-- and then on updated_at.
--;;
-- Order-attributed ad → product mapping (approach "B").
--
-- The meta_ads / google_ads writers of ad_product_mapping read the ad CREATIVE's landing
-- URL and only understand /products/{handle} and /collections/{handle}. Brands whose ads
-- land on custom pages (shopbuilt.com: /pages/natural-movement-shoe-v1) or on another
-- store get zero rows, and the RCA "Ad Overview" panel shows "No ads found".
--
-- This writer answers a different question — not "which product does the ad point at"
-- but "which products did the ad actually sell":
--
--   raw_shopify_orders / note_attributes  →  order_click_attribution.ad_id   (per order,
--        entry URL parsed + validated against advertising.ad_id — see that transform)
--   order_click_attribution ⨝ order_lines →  one row per (ad, product) with order counts
--
-- Both writers coexist in ad_product_mapping. Each DELETEs only its own source_type and
-- the PK includes source_type, so a (ad, product) pair mapped by both keeps both rows:
--   link_type='product'|'collection', source_type='meta_ads'|'google_ads'  ← creative URL
--   link_type='order',                source_type='shopify'                ← this file
-- source_type MUST be 'shopify': the PG→ClickHouse copy in etl_runner narrows the frame to
-- the running connector's source_type, and this transform runs under the shopify connector.
--
-- Full refresh per org (mappings are a summary, not a time series). The transform reads
-- whatever order_click_attribution holds, so its coverage equals that table's history.

-- Deliberately no ALTER TABLE self-migration (see the note in
-- raw_sanitized_order_click_attribution.sql: ALTER TABLE deadlocks parallel org runs).
-- Schema lives in ETL/migrations/2026-08-27_order_attributed_ad_product_mapping.sql.

DELETE FROM ad_product_mapping
WHERE organization_id = %(org)s
  AND source_type = 'shopify'
--;;
INSERT INTO ad_product_mapping (
  organization_id,
  ad_id,
  product_id,
  product_name,
  link_type,
  collection_handle,
  source_type,
  order_count,
  attributed_revenue,
  first_order_at,
  last_order_at
)
SELECT
  oca.organization_id,
  oca.ad_id::BIGINT                                   AS ad_id,
  -- order_lines.product_id is VARCHAR (Shopify GID fallout); cast after the digits-only
  -- guard in WHERE so an empty string or non-numeric value cannot break the insert.
  ol.product_id::BIGINT                               AS product_id,
  MAX(ol.product_name)                                AS product_name,
  'order'::VARCHAR(32)                                AS link_type,
  NULL::TEXT                                          AS collection_handle,
  'shopify'::VARCHAR(32)                              AS source_type,
  COUNT(DISTINCT oca.order_id)::INTEGER               AS order_count,
  SUM(
    COALESCE(ol.item_price, 0) * COALESCE(ol.quantity, 0)
    - COALESCE(ol.discount_allocated, 0)
  )::NUMERIC(18,2)                                    AS attributed_revenue,
  MIN(oca.order_date)                                 AS first_order_at,
  MAX(oca.order_date)                                 AS last_order_at
FROM order_click_attribution oca
JOIN order_lines ol
  ON ol.order_id = oca.order_id
  AND ol.organization_id = oca.organization_id
WHERE oca.organization_id = %(org)s
  AND oca.ad_platform = 'meta'
  AND oca.ad_id IS NOT NULL
  AND oca.ad_id ~ '^[0-9]+$'
  AND ol.product_id ~ '^[0-9]+$'
GROUP BY oca.organization_id, oca.ad_id, ol.product_id::BIGINT
ON CONFLICT (organization_id, ad_id, product_id, source_type)
DO UPDATE SET
  product_name       = EXCLUDED.product_name,
  link_type          = EXCLUDED.link_type,
  collection_handle  = EXCLUDED.collection_handle,
  order_count        = EXCLUDED.order_count,
  attributed_revenue = EXCLUDED.attributed_revenue,
  first_order_at     = EXCLUDED.first_order_at,
  last_order_at      = EXCLUDED.last_order_at
