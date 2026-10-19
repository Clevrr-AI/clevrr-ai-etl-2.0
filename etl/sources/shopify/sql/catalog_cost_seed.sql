-- product_cost_history rows from Shopify variant prices, one org
-- (legacy connectors/shopify/seed_product_cost_history.sql).
-- Changed: (a) closes only open 'shopify_price' rows. Legacy closed any open row whose cost
-- differed from the price, so a manual cost (never equal to the selling price) was replaced by
-- the price at the next catalog run. (b) is unchanged: it adds a 'shopify_price' row only for a
-- variant with no open row of any source, so a manual cost stays the open row.
--;;
WITH changed AS (
  SELECT
    pch.id            AS open_row_id,
    COALESCE(pv.updated_at, NOW()) AS new_effective_at
  FROM product_variants pv
  JOIN product_cost_history pch
    ON pch.organization_id = pv.organization_id
   AND pch.variant_id      = pv.variant_id
   AND pch.expires_at      IS NULL
   AND pch.source          = 'shopify_price'
  WHERE pv.organization_id = %(org)s
    AND pv.price IS NOT NULL
    AND pch.cost_per_item IS DISTINCT FROM pv.price
)
UPDATE product_cost_history pch
   SET expires_at = c.new_effective_at
  FROM changed c
 WHERE pch.id = c.open_row_id
--;;
INSERT INTO product_cost_history (
    organization_id,
    product_id,
    variant_id,
    sku,
    cost_per_item,
    currency,
    source,
    effective_at,
    expires_at,
    updated_at,
    updated_by,
    created_at
)
SELECT
    pv.organization_id,
    pv.product_id,
    pv.variant_id,
    pv.sku,
    pv.price,
    NULL,
    'shopify_price',
    CASE
      WHEN EXISTS (
        SELECT 1
          FROM product_cost_history pch
         WHERE pch.organization_id = pv.organization_id
           AND pch.variant_id      = pv.variant_id
      )
      THEN COALESCE(pv.updated_at, NOW())
      ELSE COALESCE(pv.created_at, pv.updated_at, NOW())
    END AS effective_at,
    NULL,
    NOW(),
    'etl_catalog_seed',
    NOW()
FROM product_variants pv
WHERE pv.organization_id = %(org)s
  AND pv.price IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM product_cost_history pch
      WHERE pch.organization_id = pv.organization_id
        AND pch.variant_id      = pv.variant_id
        AND pch.expires_at      IS NULL
  )
ON CONFLICT DO NOTHING
