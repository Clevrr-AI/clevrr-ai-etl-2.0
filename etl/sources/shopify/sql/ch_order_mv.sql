-- order_mv rows for the given orders of one org (ClickHouse; legacy internal/order_mv_insert.sql).
-- Changed: (1) the orders are the ones this run touched ({ids}), not "created in the window",
-- so a refund on an older order now reaches order_mv; legacy's window was also 5.5 h off
-- (IST wall-clock values compared with true instants). (2) FINAL on orders, order_lines,
-- refunds and refund_lines: they are ReplacingMergeTree and legacy read them without FINAL,
-- so unmerged versions of an order were summed twice. Values are bound ({org}, {ids}).
--;;
INSERT INTO order_mv
WITH
  ol_agg AS (
    SELECT
      organization_id,
      order_id,
      SUM(toFloat64(coalesce(quantity, 0)) * toFloat64(coalesce(item_price, 0))) AS gross_sales,
      SUM(toFloat64(coalesce(discount_allocated, 0))) AS discounts,
      SUM(toFloat64(coalesce(tax_allocated, 0))) AS order_lines_tax,
      countDistinct(order_item_id) AS line_item_count,
      SUM(coalesce(quantity, 0)) AS quantity_total
    FROM order_lines FINAL
    WHERE organization_id = {org:String} AND toString(order_id) IN {ids:Array(String)}
    GROUP BY organization_id, order_id
  ),
  refunds_agg AS (
    SELECT
      organization_id,
      order_id,
      SUM(returns) AS returns
    FROM (
      SELECT
        organization_id,
        toString(order_id) AS order_id,
        SUM(
          toFloat64(abs(coalesce(subtotal_amount, 0))) +
          toFloat64(abs(coalesce(total_tax, 0)))
        ) AS returns
      FROM refund_lines FINAL
      WHERE organization_id = {org:String} AND toString(order_id) IN {ids:Array(String)}
        AND order_id IS NOT NULL
      GROUP BY organization_id, order_id
      UNION ALL
      SELECT
        organization_id,
        order_id,
        SUM(toFloat64(abs(coalesce(refund_amount, 0)))) AS returns
      FROM refunds FINAL
      WHERE organization_id = {org:String} AND toString(order_id) IN {ids:Array(String)}
        AND refund_amount IS NOT NULL
        AND source_type != 'shopify'
        AND (kind IS NULL OR kind != 'shipping_refund')
      GROUP BY organization_id, order_id
    )
    GROUP BY organization_id, order_id
  ),
  cogs_agg AS (
    SELECT
      organization_id,
      order_id,
      round(SUM(
        toFloat64(quantity) * toFloat64(ifNull(variant_cost, ifNull(product_cost, 0)))
      ), 2) AS cogs
    FROM (
      SELECT
        ol.organization_id,
        ol.order_id,
        ol.product_id,
        ol.variant_id,
        ol.created_at,
        ol.quantity,
        argMaxIf(
          pch.cost_per_item,
          pch.effective_at,
          pch.organization_id = ol.organization_id
          AND pch.product_id = ol.product_id
          AND isNotNull(pch.variant_id)
          AND toString(pch.variant_id) = toString(ifNull(ol.variant_id, ''))
          AND pch.effective_at <= ol.created_at
          AND (pch.expires_at IS NULL OR pch.expires_at > ol.created_at)
        ) AS variant_cost,
        argMaxIf(
          pch.cost_per_item,
          pch.effective_at,
          pch.organization_id = ol.organization_id
          AND pch.product_id = ol.product_id
          AND isNull(pch.variant_id)
          AND pch.effective_at <= ol.created_at
          AND (pch.expires_at IS NULL OR pch.expires_at > ol.created_at)
        ) AS product_cost
      FROM order_lines AS ol FINAL
      LEFT JOIN product_cost_history pch
        ON pch.organization_id = ol.organization_id
       AND pch.product_id = ol.product_id
      WHERE ol.organization_id = {org:String} AND toString(order_id) IN {ids:Array(String)}
      GROUP BY
        ol.organization_id,
        ol.order_id,
        ol.product_id,
        ol.variant_id,
        ol.created_at,
        ol.quantity
    )
    GROUP BY organization_id, order_id
  ),
  packaging_agg AS (
    SELECT
      organization_id,
      order_id,
      round(SUM(
        toFloat64(quantity) * toFloat64(per_item_fee)
      ), 2) AS packaging_cost
    FROM (
      SELECT
        ol.organization_id,
        ol.order_id,
        ol.product_id,
        ol.variant_id,
        ol.created_at,
        ol.quantity,
        COALESCE(
          toFloat64(argMaxIf(
            v.value,
            COALESCE(v.date_start, v.updated_at),
            v.component = 'packaging' AND v.unit = 'per_item' AND
            v.scope_type = 'product' AND v.scope_key = ol.product_id AND
            COALESCE(v.date_start, v.updated_at) <= ol.created_at
          )),
          toFloat64(argMaxIf(
            v.value,
            COALESCE(v.date_start, v.updated_at),
            v.component = 'packaging' AND v.unit = 'per_item' AND
            v.scope_type = 'global' AND v.scope_key IS NULL AND
            COALESCE(v.date_start, v.updated_at) <= ol.created_at
          )),
          0.0
        ) AS per_item_fee
      FROM order_lines AS ol FINAL
      LEFT JOIN variable_cost_rules_versions v
        ON v.organization_id = ol.organization_id
       AND v.component = 'packaging'
       AND v.unit = 'per_item'
      WHERE ol.organization_id = {org:String} AND toString(order_id) IN {ids:Array(String)}
      GROUP BY
        ol.organization_id,
        ol.order_id,
        ol.product_id,
        ol.variant_id,
        ol.created_at,
        ol.quantity
    )
    GROUP BY organization_id, order_id
  ),
  fees_agg AS (
    SELECT
      o.organization_id,
      o.order_id,
      COALESCE(
        toFloat64(argMaxIf(
          v.value,
          COALESCE(v.date_start, v.updated_at),
          v.component = 'platform_fee' AND v.unit = 'per_order' AND
          v.scope_type = 'channel' AND v.scope_key = o.source_type AND
          COALESCE(v.date_start, v.updated_at) <= o.created_at
        )),
        toFloat64(argMaxIf(
          v.value,
          COALESCE(v.date_start, v.updated_at),
          v.component = 'platform_fee' AND v.unit = 'per_order' AND
          v.scope_type = 'global' AND v.scope_key IS NULL AND
          COALESCE(v.date_start, v.updated_at) <= o.created_at
        )),
        0.0
      ) AS platform_fees,
      COALESCE(
        toFloat64(argMaxIf(
          v.value,
          COALESCE(v.date_start, v.updated_at),
          v.component = 'txn_fee' AND v.unit = 'per_order' AND
          v.scope_type = 'channel' AND v.scope_key = o.source_type AND
          COALESCE(v.date_start, v.updated_at) <= o.created_at
        )),
        toFloat64(argMaxIf(
          v.value,
          COALESCE(v.date_start, v.updated_at),
          v.component = 'txn_fee' AND v.unit = 'per_order' AND
          v.scope_type = 'global' AND v.scope_key IS NULL AND
          COALESCE(v.date_start, v.updated_at) <= o.created_at
        )),
        0.0
      ) AS txn_fee
    FROM orders AS o FINAL
    LEFT JOIN variable_cost_rules_versions v
      ON v.organization_id = o.organization_id
     AND v.component IN ('platform_fee', 'txn_fee')
     AND v.unit = 'per_order'
    WHERE o.organization_id = {org:String} AND toString(o.order_id) IN {ids:Array(String)}
    GROUP BY
      o.organization_id,
      o.order_id,
      o.source_type,
      o.created_at
  )
SELECT
  o.organization_id,
  o.order_id,
  o.order_date,
  o.created_at,
  o.source_type,
  o.customer_id,
  o.customer_name,
  o.financial_status,
  o.fulfillment_status,
  o.cancelled_at,
  o.currency,
  o.total_price,
  o.shipping_price,
  o.tax_price,
  ol.gross_sales,
  ol.discounts,
  r.returns,
  round(coalesce(ol.gross_sales, 0) - coalesce(r.returns, 0) - coalesce(ol.discounts, 0), 2) AS net_sales,
  coalesce(o.tax_price, 0) AS order_tax,
  c.cogs,
  p.packaging_cost,
  f.platform_fees,
  f.txn_fee,
  round(
    (coalesce(ol.gross_sales, 0) - coalesce(r.returns, 0) - coalesce(ol.discounts, 0))
    - coalesce(c.cogs, 0)
    - coalesce(p.packaging_cost, 0)
    - coalesce(f.platform_fees, 0),
    2
  ) AS contribution_profit,
  if(
    coalesce(ol.gross_sales, 0) > 0,
    round(
      (
        (
          (coalesce(ol.gross_sales, 0) - coalesce(r.returns, 0) - coalesce(ol.discounts, 0))
          - coalesce(c.cogs, 0)
          - coalesce(p.packaging_cost, 0)
          - coalesce(f.platform_fees, 0)
        ) / coalesce(ol.gross_sales, 1)
      ) * 100,
      2
    ),
    0
  ) AS contribution_margin,
  ol.line_item_count,
  ol.quantity_total,
  now('Asia/Kolkata') AS updated_at
FROM orders AS o FINAL
LEFT JOIN ol_agg ol
  ON ol.organization_id = o.organization_id
 AND ol.order_id = o.order_id
LEFT JOIN refunds_agg r
  ON r.organization_id = o.organization_id
 AND r.order_id = o.order_id
LEFT JOIN cogs_agg c
  ON c.organization_id = o.organization_id
 AND c.order_id = o.order_id
LEFT JOIN packaging_agg p
  ON p.organization_id = o.organization_id
 AND p.order_id = o.order_id
LEFT JOIN fees_agg f
  ON f.organization_id = o.organization_id
 AND f.order_id = o.order_id
WHERE o.organization_id = {org:String} AND toString(o.order_id) IN {ids:Array(String)}
SETTINGS distributed_product_mode = 'global'
