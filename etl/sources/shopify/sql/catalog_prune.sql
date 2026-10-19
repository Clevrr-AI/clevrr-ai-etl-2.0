-- Drop the products and variants Shopify no longer returns, one org. The extract step already
-- made raw_shopify_products the org's full current catalog (after a complete fetch only).
-- Legacy deleted the whole catalog every day instead (fetch_runner.wipe_catalog_psql), which
-- also erased the app's manual costs.
--;;
DELETE FROM products p
WHERE p.organization_id = %(org)s
  AND p.source_type = 'shopify'
  AND NOT EXISTS (
    SELECT 1 FROM raw_shopify_products rp
    WHERE rp.organization_id = p.organization_id AND rp.id = p.product_id
  )
--;;
DELETE FROM product_variants pv
WHERE pv.organization_id = %(org)s
  AND pv.source_type = 'shopify'
  AND NOT EXISTS (
    SELECT 1
    FROM raw_shopify_products rp
    CROSS JOIN LATERAL jsonb_array_elements(COALESCE(rp.variants, '[]'::jsonb)) AS variant
    WHERE rp.organization_id = pv.organization_id
      AND (variant ->> 'id')::bigint = pv.variant_id
  )
