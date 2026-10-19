-- product_variants from the variants inside raw_shopify_products, one org
-- (legacy connectors/shopify/raw_sanitized_product_variants.sql, unchanged but for the
-- bound org). cost_per_item is the app's manual cost: inserted NULL, never overwritten.
--;;
WITH src AS (
  SELECT
    rp.id::bigint AS product_id,
    rp.organization_id AS organization_id,
    rp.variants::jsonb AS variants_json,
    rp.images::jsonb AS images_json,
    NULLIF(rp.image ->> 'src', '') AS default_image_url,
    rp.created_at AS product_created_at,
    rp.updated_at AS product_updated_at
  FROM raw_shopify_products rp
  WHERE rp.organization_id = %(org)s
),
variant_rows AS (
  SELECT
    (variant ->> 'id')::bigint AS variant_id,
    s.product_id,
    s.organization_id,
    NULLIF(btrim(variant ->> 'title'), '') AS title,
    NULLIF(btrim(variant ->> 'sku'), '') AS sku,
    (NULLIF(variant ->> 'position', ''))::int AS position,
    (NULLIF(variant ->> 'price', ''))::numeric(14,2) AS price,
    (NULLIF(variant ->> 'compare_at_price', ''))::numeric(14,2) AS compare_at_price,
    NULLIF(btrim(variant ->> 'option1'), '') AS option1,
    NULLIF(btrim(variant ->> 'option2'), '') AS option2,
    NULLIF(btrim(variant ->> 'option3'), '') AS option3,
    (NULLIF(variant ->> 'inventory_quantity', ''))::int AS inventory_quantity,
    (NULLIF(variant ->> 'inventory_item_id', ''))::bigint AS inventory_item_id,
    CASE WHEN variant ? 'requires_shipping'
         THEN (variant ->> 'requires_shipping')::boolean
         ELSE NULL END AS requires_shipping,
    CASE WHEN variant ? 'taxable'
         THEN (variant ->> 'taxable')::boolean
         ELSE NULL END AS taxable,
    (NULLIF(variant ->> 'image_id', ''))::bigint AS image_id,
    COALESCE(
      (
        SELECT img ->> 'src'
        FROM jsonb_array_elements(COALESCE(s.images_json, '[]'::jsonb)) AS img
        WHERE (NULLIF(img ->> 'id', ''))::bigint = (NULLIF(variant ->> 'image_id', ''))::bigint
        LIMIT 1
      ),
      (
        SELECT img ->> 'src'
        FROM jsonb_array_elements(COALESCE(s.images_json, '[]'::jsonb)) AS img
        WHERE EXISTS (
          SELECT 1
          FROM jsonb_array_elements_text(COALESCE(img -> 'variant_ids', '[]'::jsonb)) AS vid(variant_id_text)
          WHERE NULLIF(vid.variant_id_text, '')::bigint = (variant ->> 'id')::bigint
        )
        LIMIT 1
      ),
      s.default_image_url
    ) AS image_url,
    (NULLIF(variant ->> 'created_at', ''))::timestamptz AS variant_created_at,
    (NULLIF(variant ->> 'updated_at', ''))::timestamptz AS variant_updated_at,
    (NULLIF(variant ->> 'cost', ''))::numeric(18,2) AS shopify_cost,
    s.product_created_at,
    s.product_updated_at
  FROM src s
  CROSS JOIN LATERAL jsonb_array_elements(s.variants_json) AS variant
),
ranked AS (
  SELECT
    vr.*,
    ROW_NUMBER() OVER (
      PARTITION BY vr.organization_id, vr.product_id
      ORDER BY COALESCE(vr.position, 2147483647), vr.variant_id
    ) AS rn
  FROM variant_rows vr
),
prepared AS (
  SELECT
    variant_id,
    organization_id,
    product_id,
    title,
    sku,
    position,
    price,
    compare_at_price,
    option1,
    option2,
    option3,
    inventory_quantity,
    inventory_item_id,
    requires_shipping,
    taxable,
    variant_created_at,
    variant_updated_at,
    shopify_cost,
    image_url,
    rn = 1 AS is_default
  FROM ranked
  WHERE variant_id IS NOT NULL
),
-- A variant_id can appear in more than one Shopify product due to data quality issues.
-- The PK on product_variants is (variant_id, organization_id), so deduplicate on that
-- composite key to avoid a same-org variant colliding on insert.
deduped AS (
  SELECT DISTINCT ON (variant_id, organization_id)
    variant_id, organization_id, product_id, title, sku, position,
    price, compare_at_price, option1, option2, option3,
    inventory_quantity, inventory_item_id, requires_shipping, taxable,
    variant_created_at, variant_updated_at, shopify_cost, image_url,
    is_default
  FROM prepared
  ORDER BY variant_id, organization_id, product_id ASC
)
INSERT INTO product_variants (
  variant_id,
  organization_id,
  product_id,
  title,
  sku,
  position,
  price,
  compare_at_price,
  option1,
  option2,
  option3,
  image_url,
  inventory_quantity,
  inventory_item_id,
  requires_shipping,
  taxable,
  created_at,
  updated_at,
  cost_per_item,
  shopify_cost_per_item,
  source_type,
  is_default
)
SELECT
  d.variant_id,
  d.organization_id,
  d.product_id,
  d.title,
  d.sku,
  d.position,
  d.price,
  d.compare_at_price,
  d.option1,
  d.option2,
  d.option3,
  d.image_url,
  d.inventory_quantity,
  d.inventory_item_id,
  d.requires_shipping,
  d.taxable,
  d.variant_created_at,
  d.variant_updated_at,
  NULL::numeric(18,2) AS cost_per_item,
  d.shopify_cost,
  'shopify',
  d.is_default
FROM deduped d
ON CONFLICT (variant_id, organization_id) DO UPDATE SET
  product_id            = EXCLUDED.product_id,
  title                 = EXCLUDED.title,
  sku                   = EXCLUDED.sku,
  position              = EXCLUDED.position,
  price                 = EXCLUDED.price,
  compare_at_price      = EXCLUDED.compare_at_price,
  option1               = EXCLUDED.option1,
  option2               = EXCLUDED.option2,
  option3               = EXCLUDED.option3,
  image_url             = EXCLUDED.image_url,
  inventory_quantity    = EXCLUDED.inventory_quantity,
  inventory_item_id     = EXCLUDED.inventory_item_id,
  requires_shipping     = EXCLUDED.requires_shipping,
  taxable               = EXCLUDED.taxable,
  updated_at            = EXCLUDED.updated_at,
  -- Preserve API-set costs (record_product_cost_change writes here directly).
  -- Only fill in cost_per_item if the variant row doesn't already have one.
  cost_per_item         = COALESCE(product_variants.cost_per_item, EXCLUDED.cost_per_item),
  shopify_cost_per_item = EXCLUDED.shopify_cost_per_item,
  source_type           = EXCLUDED.source_type,
  is_default            = EXCLUDED.is_default
