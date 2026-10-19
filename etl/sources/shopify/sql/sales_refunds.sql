-- refunds for one org (legacy connectors/shopify/raw_sanitized_refunds.sql).
-- Ported by rule: values are bound (%(org)s, %(exclude_pos)s, %(since)s,
-- %(created_from)s, %(created_to)s) instead of pasted ${...} text, a literal percent sign
-- is doubled, and the app's tables are read from public.
-- The window: orders updated since %(since)s (an incremental run) or created in
-- [%(created_from)s, %(created_to)s) (a backfill); legacy ran each file twice, on created_at
-- and then on updated_at.
-- Changed: raw-to-raw joins also match organization_id (legacy matched id and source_type
-- only, so two orgs holding the same Shopify ids, e.g. a demo copy, mixed rows).
--;;
INSERT INTO refunds (
  refund_id,
  order_id,
  source_type,
  refund_date,
  refund_amount,
  shipping_refund,
  shipping_refund_tax,
  created_at,
  updated_at,
  city,
  kind,
  organization_id,
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
WITH adjustment_agg AS (
  SELECT
    rr.id AS refund_id,
    rr.organization_id,
    rr.source_type,
    COALESCE(
      SUM(
        CASE
          WHEN adj.elem IS NOT NULL
               AND ((adj.elem ->> 'kind') IS NULL OR (adj.elem ->> 'kind') <> 'shipping_refund')
            THEN NULLIF((adj.elem ->> 'amount'), '')::NUMERIC
          ELSE 0::NUMERIC
        END
      ),
      0::NUMERIC
    )::NUMERIC(14,2) AS non_shipping_amount,
    COALESCE(
      SUM(
        CASE
          WHEN adj.elem IS NOT NULL
               AND (adj.elem ->> 'kind') = 'shipping_refund'
            THEN NULLIF((adj.elem ->> 'tax_amount'), '')::NUMERIC
          ELSE 0::NUMERIC
        END
      ),
      0::NUMERIC
    )::NUMERIC(14,2) AS shipping_tax_amount,
    -- Signed shipping-refund amount (negative = money returned to the customer).
    -- Summed over ALL shipping_refund adjustments, so refunds whose kind resolves
    -- to 'mixed_refund' keep their shipping portion here too.
    COALESCE(
      SUM(
        CASE
          WHEN adj.elem IS NOT NULL
               AND (adj.elem ->> 'kind') = 'shipping_refund'
            THEN NULLIF((adj.elem ->> 'amount'), '')::NUMERIC
          ELSE 0::NUMERIC
        END
      ),
      0::NUMERIC
    )::NUMERIC(14,2) AS shipping_amount,
    SUM(
      CASE
        WHEN adj.elem IS NOT NULL AND (adj.elem ->> 'kind') = 'shipping_refund' THEN 1
        ELSE 0
      END
    ) AS shipping_adj_count,
    SUM(
      CASE
        WHEN adj.elem IS NOT NULL
             AND ((adj.elem ->> 'kind') IS NULL OR (adj.elem ->> 'kind') <> 'shipping_refund')
          THEN 1
        ELSE 0
      END
    ) AS non_shipping_adj_count,
    MIN(
      CASE
        WHEN adj.elem IS NOT NULL
             AND ((adj.elem ->> 'kind') IS NULL OR (adj.elem ->> 'kind') <> 'shipping_refund')
          THEN NULLIF((adj.elem ->> 'kind'), '')
        ELSE NULL
      END
    ) AS non_shipping_kind
  FROM raw_shopify_refunds rr
  LEFT JOIN LATERAL jsonb_array_elements(COALESCE(rr.order_adjustments::jsonb, '[]'::jsonb)) AS adj(elem) ON TRUE
  GROUP BY rr.id, rr.organization_id, rr.source_type
)
SELECT
  rr.id AS refund_id,
  rr.order_id AS order_id,
  'shopify' AS source_type,
  (COALESCE(rr.processed_at, rr.created_at)) AS refund_date,
  aa.non_shipping_amount AS refund_amount,
  aa.shipping_amount AS shipping_refund,
  aa.shipping_tax_amount AS shipping_refund_tax,
  rr.created_at as created_at,
  COALESCE(ro.updated_at, rr.processed_at, rr.created_at) as updated_at,
  (ro.shipping_address::jsonb ->> 'city') AS city,
  CASE
    WHEN aa.shipping_adj_count > 0 AND aa.non_shipping_adj_count = 0 THEN 'shipping_refund'
    WHEN aa.non_shipping_adj_count > 0 AND aa.shipping_adj_count = 0 THEN aa.non_shipping_kind
    WHEN aa.non_shipping_adj_count > 0 AND aa.shipping_adj_count > 0 THEN 'mixed_refund'
    ELSE NULL
  END AS kind,

  COALESCE(rr.organization_id, ro.organization_id) AS organization_id,
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
FROM raw_shopify_refunds rr
JOIN raw_shopify_orders ro ON rr.order_id = ro.id AND rr.source_type = ro.source_type
  AND rr.organization_id = ro.organization_id
LEFT JOIN adjustment_agg aa
      ON aa.refund_id = rr.id
      AND aa.organization_id = rr.organization_id
      AND aa.source_type = rr.source_type
WHERE ro.organization_id = %(org)s
AND (NOT %(exclude_pos)s OR upper(coalesce(ro.source_name, '')) <> 'POS')
AND (ro.updated_at >= %(since)s OR (ro.created_at >= %(created_from)s AND ro.created_at < %(created_to)s))
ON CONFLICT (refund_id, organization_id) DO UPDATE SET
  order_id = EXCLUDED.order_id,
  source_type = EXCLUDED.source_type,
  refund_date = EXCLUDED.refund_date,
  refund_amount = EXCLUDED.refund_amount,
  shipping_refund = EXCLUDED.shipping_refund,
  shipping_refund_tax = EXCLUDED.shipping_refund_tax,
  created_at = EXCLUDED.created_at,
  updated_at = EXCLUDED.updated_at,
  city = EXCLUDED.city,
  kind = EXCLUDED.kind,
  discount_code = EXCLUDED.discount_code,
  state = EXCLUDED.state,
  zip = EXCLUDED.zip,
  product_full_url = EXCLUDED.product_full_url,
  customer_name = EXCLUDED.customer_name,
  order_status_url = EXCLUDED.order_status_url,
  last_synced = CURRENT_TIMESTAMP
