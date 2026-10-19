-- order_lines for one org (legacy connectors/shopify/raw_sanitized_order_lines.sql).
-- Ported by rule: values are bound (%(org)s, %(exclude_pos)s, %(since)s,
-- %(created_from)s, %(created_to)s) instead of pasted ${...} text, a literal percent sign
-- is doubled, and the app's tables are read from public.
-- The window: orders updated since %(since)s (an incremental run) or created in
-- [%(created_from)s, %(created_to)s) (a backfill); legacy ran each file twice, on created_at
-- and then on updated_at.
-- Changed: raw-to-raw joins also match organization_id (legacy matched id and source_type
-- only, so two orgs holding the same Shopify ids, e.g. a demo copy, mixed rows).
--;;
INSERT INTO order_lines (
  order_item_id,
  order_id,
  source_type,
  variant_id,
  quantity,
  item_price,
  discount_allocated,
  tax_allocated,
  product_id,
  product_name,
  city,
  created_at,
  organization_id,
  sale_initiated_category,
  discount_code,
  state,
  zip,
  product_full_url,
  customer_name,
  order_status_url,
  updated_at,
  utm_source_raw,
  mapped_source_category
)
-- Custom UTM mapping (Settings › UTM Mapping). Same wrapper as raw_sanitized_orders.sql:
-- the original transform is untouched inside the subquery; the wrapper copies the parent
-- order's utm_source_raw and mapped_source_category onto every line. Copying, not
-- recomputing, keeps one source of truth — the orders transform runs before this one in
-- etl_registry, so the parent row is always there. Lines are what every Sales-by-Source
-- metric aggregates, which is why the columns must exist here too.
SELECT
  src.*,
  o.utm_source_raw,
  COALESCE(o.mapped_source_category, src.sale_initiated_category) AS mapped_source_category
FROM (
-- Org filter policy (2026-09-21): the WHERE clauses below compare the raw row's own
-- organization_id directly. Wrapping the column in COALESCE over a LEFT JOIN made the
-- predicate non-sargable (no index, no per-org selectivity), which fed the rows=1
-- misestimate behind the 2026-09-18..20 nightly failures.
-- 2026-10-07: the Hevo-era account-to-org fallback join was removed. Every raw row now
-- carries its own organization_id, so no lookup table is consulted here any more.
SELECT
  rol.id AS order_item_id,
  rol.order_id AS order_id,
  'shopify' AS source_type,
  rol.variant_id,
  rol.quantity,
  rol.price::NUMERIC(14,2) AS item_price,
  (
    -- Sum ALL discount allocations: a line can carry several (discount code +
    -- automatic discount). Reading only element 0 dropped the rest.
    SELECT COALESCE(SUM((elem ->> 'amount')::NUMERIC), 0)
    FROM jsonb_array_elements(COALESCE(rol.discount_allocations::jsonb, '[]'::jsonb)) elem
  )::NUMERIC(14,2) AS discount_allocated,
  (
    SELECT SUM((elem ->> 'price')::NUMERIC)
    FROM jsonb_array_elements(rol.tax_lines::jsonb) elem
  ) AS tax_allocated,
  rol.product_id,
  rol.title AS product_name,
  (ro.shipping_address::jsonb ->> 'city') AS city,
  (ro.created_at AT TIME ZONE 'Asia/Kolkata') as created_at,
  COALESCE(rol.organization_id, ro.organization_id) AS organization_id,
  -- The channel verdict is computed ONCE, on the parent order, by the rule table in
  -- raw_sanitized_orders.sql (docs/attribution-channel-rules.md). Lines copy it: the orders
  -- transform runs first in etl_registry, so the parent row is always there. A line whose
  -- parent resolved to a different org (never seen in practice) gets DRAFT, the "nothing
  -- observed" verdict, rather than a guessed channel. Until 2026-09-21 this file carried its
  -- own copy of the CASE and had already drifted from the orders one.
  COALESCE(po.sale_initiated_category, 'DRAFT') AS sale_initiated_category,
  (ro.discount_applications::jsonb -> 0 ->> 'code') AS discount_code,
  (ro.shipping_address::jsonb ->> 'province') AS state,
  (ro.shipping_address::jsonb ->> 'zip') AS zip,
  CASE
    WHEN rol.product_id IS NOT NULL AND ro.order_status_url IS NOT NULL THEN
      'https://admin.shopify.com/store/'
      || split_part(ro.order_status_url, '/', 4)
      || '/products/' || rol.product_id::text
    ELSE NULL
   END AS product_full_url,
  CONCAT(
    COALESCE(ro.customer::jsonb->>'first_name', ''),
    ' ',
    COALESCE(ro.customer::jsonb->>'last_name', '')
  ) AS customer_name,
  CASE
    WHEN ro.order_status_url IS NOT NULL THEN
      'https://admin.shopify.com/store/'
      || split_part(ro.order_status_url, '/', 4)
      || '/orders/' || ro.id::text
    ELSE NULL
  END AS order_status_url,
  (ro.updated_at AT TIME ZONE 'Asia/Kolkata') as updated_at
FROM raw_shopify_order_lines rol
JOIN raw_shopify_orders ro
  ON rol.order_id = ro.id
  AND rol.source_type = ro.source_type
  AND rol.organization_id = ro.organization_id
LEFT JOIN orders po
  ON po.order_id = rol.order_id
 AND po.organization_id = rol.organization_id
WHERE rol.organization_id IS NOT NULL
  AND rol.organization_id = %(org)s
  AND (NOT %(exclude_pos)s OR upper(coalesce(ro.source_name, '')) <> 'POS')
  AND (ro.updated_at >= %(since)s OR (ro.created_at >= %(created_from)s AND ro.created_at < %(created_to)s))
) src
LEFT JOIN orders o
  ON o.order_id        = src.order_id
 AND o.organization_id = src.organization_id
ON CONFLICT (order_item_id, organization_id) DO UPDATE SET
  order_id = EXCLUDED.order_id,
  source_type = EXCLUDED.source_type,
  variant_id = EXCLUDED.variant_id,
  quantity = EXCLUDED.quantity,
  item_price = EXCLUDED.item_price,
  discount_allocated = EXCLUDED.discount_allocated,
  tax_allocated = EXCLUDED.tax_allocated,
  product_id = EXCLUDED.product_id,
  product_name = EXCLUDED.product_name,
  created_at = EXCLUDED.created_at,
  city = EXCLUDED.city,
  sale_initiated_category = EXCLUDED.sale_initiated_category,
  discount_code = EXCLUDED.discount_code,
  state = EXCLUDED.state,
  zip = EXCLUDED.zip,
  product_full_url = EXCLUDED.product_full_url,
  customer_name = EXCLUDED.customer_name,
  order_status_url = EXCLUDED.order_status_url,
  updated_at = EXCLUDED.updated_at,
  utm_source_raw = EXCLUDED.utm_source_raw,
  mapped_source_category = EXCLUDED.mapped_source_category,
  last_synced = CURRENT_TIMESTAMP
