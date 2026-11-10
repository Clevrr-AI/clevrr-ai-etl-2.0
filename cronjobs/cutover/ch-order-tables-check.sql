-- READ-ONLY check before ch-order-tables-fix.sql: does prod's order_mv_shard match the new table?
-- Both lists must be [] before the fix runs. Run it the same way as the fix (docs/prod-cutover.md).
SELECT 'order_mv: columns the new table has but prod lacks (the copy would fail)' AS check,
       arrayFilter(c -> NOT has((SELECT groupArray(name) FROM system.columns WHERE database = 'clevrr_db' AND table = 'order_mv_shard'), c), ['organization_id', 'order_id', 'order_date', 'created_at', 'source_type', 'customer_id', 'customer_name', 'financial_status', 'fulfillment_status', 'cancelled_at', 'currency', 'total_price', 'shipping_price', 'tax_price', 'gross_sales', 'discounts', 'returns', 'net_sales', 'order_tax', 'cogs', 'packaging_cost', 'platform_fees', 'txn_fee', 'contribution_profit', 'contribution_margin', 'line_item_count', 'quantity_total', 'updated_at']) AS result
FORMAT Vertical;
SELECT 'order_mv: columns prod has that the new table would drop' AS check,
       arrayFilter(c -> NOT has(['organization_id', 'order_id', 'order_date', 'created_at', 'source_type', 'customer_id', 'customer_name', 'financial_status', 'fulfillment_status', 'cancelled_at', 'currency', 'total_price', 'shipping_price', 'tax_price', 'gross_sales', 'discounts', 'returns', 'net_sales', 'order_tax', 'cogs', 'packaging_cost', 'platform_fees', 'txn_fee', 'contribution_profit', 'contribution_margin', 'line_item_count', 'quantity_total', 'updated_at'], c), (SELECT groupArray(name) FROM system.columns WHERE database = 'clevrr_db' AND table = 'order_mv_shard')) AS result
FORMAT Vertical;
SELECT 'engines now' AS check, groupArray(concat(name, ' = ', engine)) AS result
FROM system.tables WHERE database = 'clevrr_db' AND name IN ('order_mv', 'order_mv_shard', 'order_click_attribution', 'order_click_attribution_shard', 'order_mv_shard_legacy', 'order_click_attribution_shard_legacy')
FORMAT Vertical;
SELECT 'order_mv rows on every replica (old table)' AS check, count() AS result
FROM clusterAllReplicas('clevrr-cluster', clevrr_db.order_mv_shard)
FORMAT Vertical;
