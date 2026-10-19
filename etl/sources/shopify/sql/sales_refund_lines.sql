-- refund_lines for one org (legacy connectors/shopify/raw_sanitized_refund_lines.sql).
-- Ported by rule: values are bound (%(org)s, %(exclude_pos)s, %(since)s,
-- %(created_from)s, %(created_to)s) instead of pasted ${...} text, a literal percent sign
-- is doubled, and the app's tables are read from public.
-- The window: orders updated since %(since)s (an incremental run) or created in
-- [%(created_from)s, %(created_to)s) (a backfill); legacy ran each file twice, on created_at
-- and then on updated_at.
-- Changed: raw-to-raw joins also match organization_id (legacy matched id and source_type
-- only, so two orgs holding the same Shopify ids, e.g. a demo copy, mixed rows).
--;;
INSERT INTO refund_lines (
  id,
  quantity,
  subtotal_amount,
  total_tax,
  order_lines_id,
  order_id,
  refund_id,
  organization_id,
  source_type,
  created_at,
  updated_at,
  product_id,
  product_name,
  city,
  discount_code,
  state,
  zip,
  product_full_url,
  customer_name,
  order_status_url
)
-- Org filter policy (2026-09-21): the WHERE clauses below compare the raw row's own
-- organization_id directly. Wrapping the column in COALESCE over a LEFT JOIN made the
-- predicate non-sargable (no index, no per-org selectivity), which fed the rows=1
-- misestimate behind the 2026-09-18..20 nightly failures.
-- 2026-10-07: the Hevo-era account-to-org fallback join was removed. Every raw row now
-- carries its own organization_id, so no lookup table is consulted here any more.
SELECT
  rorl.id,
  rorl.quantity,
  rorl.subtotal::NUMERIC(14,2),
  rorl.total_tax::NUMERIC(14,2),
  rorl.line_item_id AS order_lines_id,
  rorl.order_id,
  rorl.refund_id,
  COALESCE(rorl.organization_id, rr.organization_id, ro.organization_id) AS organization_id,
  rorl.source_type,
  rr.created_at as created_at,
  COALESCE(ro.updated_at, rr.processed_at, rr.created_at) as updated_at,
  (rorl.line_item::jsonb ->> 'product_id') AS product_id,
  (rorl.line_item::jsonb ->> 'title') AS product_name,
  (ro.shipping_address::jsonb ->> 'city') AS city,
  (ro.discount_applications::jsonb -> 0 ->> 'code') AS discount_code,
  (ro.shipping_address::jsonb ->> 'province') AS state,
  (ro.shipping_address::jsonb ->> 'zip') AS zip,
  (
    SELECT na_elem->>'value'
    FROM jsonb_array_elements(COALESCE(ro.note_attributes::jsonb, '[]'::jsonb)) AS na_elem
    WHERE LOWER(BTRIM(na_elem->>'name')) = 'full_url'
    LIMIT 1
  ) AS product_full_url,
  CONCAT(
    COALESCE(ro.customer::jsonb->>'first_name', ''),
    ' ',
    COALESCE(ro.customer::jsonb->>'last_name', '')
  ) AS customer_name,
  ro.order_status_url AS order_status_url
FROM raw_shopify_order_refund_lines rorl
LEFT JOIN raw_shopify_refunds rr
  ON rorl.refund_id = rr.id AND rorl.source_type = rr.source_type
  AND rorl.organization_id = rr.organization_id
JOIN raw_shopify_orders ro ON rorl.order_id = ro.id AND rorl.source_type = ro.source_type
  AND rorl.organization_id = ro.organization_id
WHERE ro.organization_id = %(org)s
AND (NOT %(exclude_pos)s OR upper(coalesce(ro.source_name, '')) <> 'POS')
AND (ro.updated_at >= %(since)s OR (ro.created_at >= %(created_from)s AND ro.created_at < %(created_to)s))
ON CONFLICT (id, organization_id) DO UPDATE SET
  quantity = EXCLUDED.quantity,
  subtotal_amount = EXCLUDED.subtotal_amount,
  total_tax = EXCLUDED.total_tax,
  order_lines_id = EXCLUDED.order_lines_id,
  order_id = EXCLUDED.order_id,
  refund_id = EXCLUDED.refund_id,
  organization_id = EXCLUDED.organization_id,
  created_at = EXCLUDED.created_at,
  updated_at = EXCLUDED.updated_at,
  product_id = EXCLUDED.product_id,
  product_name = EXCLUDED.product_name,
  city = EXCLUDED.city,
  discount_code = EXCLUDED.discount_code,
  state = EXCLUDED.state,
  zip = EXCLUDED.zip,
  product_full_url = EXCLUDED.product_full_url,
  customer_name = EXCLUDED.customer_name,
  order_status_url = EXCLUDED.order_status_url,
  last_synced = CURRENT_TIMESTAMP
