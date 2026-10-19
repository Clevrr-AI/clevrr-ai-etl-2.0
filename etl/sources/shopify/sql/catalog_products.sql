-- products from raw_shopify_products, one org (legacy connectors/shopify/raw_sanitized_products.sql).
-- Runs before catalog_variants.sql: the cost rule below reads the variants' previous Shopify cost.
-- Kept from legacy for parity: created_at/updated_at go through AT TIME ZONE 'UTC' AT TIME ZONE
-- 'Asia/Kolkata' (5.5 h early; fix after parity).
-- Changed: cost_per_item follows Shopify while it still equals the default variant's previous
-- Shopify cost, and keeps a value the app set by hand. Legacy kept any value (COALESCE) and
-- relied on its daily wipe to refresh it, which also erased manual costs.
--;;
WITH src AS (
  SELECT
    rp.id::bigint AS product_id,
    rp.organization_id AS organization_id,
    NULLIF(btrim(rp.title), '')           AS title,
    NULLIF(btrim(rp.handle), '')          AS handle,
    NULLIF(btrim(rp.status), '')          AS status,
    NULLIF(btrim(rp.vendor), '')          AS vendor,
    NULLIF(btrim(rp.product_type), '')    AS product_type,
    NULLIF(btrim(rp.tags), '')            AS tags,
    (rp.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata') AS created_at,
    (rp.updated_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata') AS updated_at,
    COALESCE(rp.image::jsonb ->> 'src', NULL) AS image_url,
    COALESCE(jsonb_array_length(rp.variants::jsonb), 0) AS variants_count,
    (COALESCE(jsonb_array_length(rp.variants::jsonb),0) > 1) AS has_variants,
    COALESCE(jsonb_array_length(rp.options::jsonb), 0) AS options_count,
    default_variant.variant_id AS default_variant_id,
    default_variant.sku AS default_sku,
    default_variant.price AS default_price,
    COALESCE(oc_slug.shopify_slug, NULLIF(btrim(rp.organization_id), '')) AS store_slug,
    CASE
      WHEN COALESCE(oc_slug.shopify_slug, NULLIF(btrim(rp.organization_id), '')) IS NOT NULL THEN
        'https://admin.shopify.com/store/' ||
        COALESCE(oc_slug.shopify_slug, NULLIF(btrim(rp.organization_id), '')) ||
        '/products/' || rp.id::text
      ELSE NULL
    END AS product_full_url,
    'shopify' AS source_type,
    default_variant.shopify_cost AS cost_per_item,
    coll.collection_name
  FROM raw_shopify_products rp
  LEFT JOIN LATERAL (
    SELECT
      (variant ->> 'id')::bigint AS variant_id,
      NULLIF(btrim(variant ->> 'sku'), '') AS sku,
      (NULLIF(variant ->> 'price', ''))::numeric(14,2) AS price,
      (NULLIF(variant ->> 'position', ''))::int AS position,
      (NULLIF(variant ->> 'updated_at', ''))::timestamptz AS updated_at,
      (NULLIF(variant ->> 'cost', ''))::numeric(18,2) AS shopify_cost
    FROM jsonb_array_elements(rp.variants::jsonb) variant
    ORDER BY
      COALESCE((variant ->> 'position')::int, 2147483647),
      (variant ->> 'id')::bigint
    LIMIT 1
  ) default_variant ON TRUE
  LEFT JOIN LATERAL (
    SELECT
      string_agg(
        DISTINCT NULLIF(btrim(COALESCE(col.title, rc.collection_id::text)), ''),
        ', '
        ORDER BY NULLIF(btrim(COALESCE(col.title, rc.collection_id::text)), '')
      ) AS collection_name
    FROM raw_shopify_collects rc
    LEFT JOIN raw_shopify_collections col
      ON col.collection_id = rc.collection_id
     AND col.organization_id = rp.organization_id
    WHERE rc.product_id = rp.id
      AND rc.organization_id = rp.organization_id
  ) coll ON TRUE
  LEFT JOIN LATERAL (
    SELECT
      NULLIF(
        regexp_replace(
          lower(NULLIF(btrim((oc.encrypted_api_credentials::jsonb ->> 'url')), '')),
          '^https?://([^./]+).*$', '\1'
        ),
        ''
      ) AS shopify_slug
    FROM public.organization_connectors oc
    WHERE oc.organization_id = rp.organization_id
      AND lower(oc.connector_name) = 'shopify'
    ORDER BY oc.updated_at DESC NULLS LAST, oc.created_at DESC NULLS LAST, oc.organization_connector_id DESC
    LIMIT 1
  ) oc_slug ON TRUE
  WHERE rp.organization_id = %(org)s
),
deduped AS (
  SELECT DISTINCT ON (product_id, organization_id)
    product_id, organization_id, title, handle, status, vendor, product_type, tags,
    created_at, updated_at, image_url, variants_count, has_variants, options_count,
    default_price, product_full_url, source_type, default_sku, cost_per_item,
    default_variant_id, collection_name
  FROM src
  ORDER BY product_id, organization_id
)
INSERT INTO products (
  product_id, organization_id, title, handle, status, vendor, product_type, tags,
  created_at, updated_at, image_url, variants_count, has_variants, options_count,
  default_price, product_full_url, source_type, sku_id, cost_per_item, default_variant_id, collection_name
)
SELECT
  product_id, organization_id, title, handle, status, vendor, product_type, tags,
  created_at, updated_at, image_url, variants_count, has_variants, options_count,
  default_price, product_full_url, source_type, default_sku, cost_per_item, default_variant_id, collection_name
FROM deduped
ON CONFLICT (product_id, organization_id) DO UPDATE SET
  title              = EXCLUDED.title,
  handle             = EXCLUDED.handle,
  status             = EXCLUDED.status,
  vendor             = EXCLUDED.vendor,
  product_type       = EXCLUDED.product_type,
  tags               = EXCLUDED.tags,
  updated_at         = EXCLUDED.updated_at,
  image_url          = EXCLUDED.image_url,
  variants_count     = EXCLUDED.variants_count,
  has_variants       = EXCLUDED.has_variants,
  options_count      = EXCLUDED.options_count,
  default_price      = EXCLUDED.default_price,
  product_full_url   = EXCLUDED.product_full_url,
  source_type        = EXCLUDED.source_type,
  sku_id             = EXCLUDED.sku_id,
  cost_per_item      = CASE
    WHEN products.cost_per_item IS NULL THEN EXCLUDED.cost_per_item
    WHEN products.cost_per_item IS NOT DISTINCT FROM (
      SELECT pv.shopify_cost_per_item FROM product_variants pv
      WHERE pv.organization_id = products.organization_id
        AND pv.variant_id = products.default_variant_id
    ) THEN EXCLUDED.cost_per_item
    ELSE products.cost_per_item
  END,
  default_variant_id = EXCLUDED.default_variant_id,
  collection_name    = EXCLUDED.collection_name
