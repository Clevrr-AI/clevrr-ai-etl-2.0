-- READ-ONLY check after ch-order-tables-fix.sql. Good: both *_shard tables are Replicated*,
-- and each "new" count equals its "old, distinct" count (the old shard tables stay as *_legacy).
SELECT 'engines' AS check, groupArray(concat(name, ' = ', engine)) AS result
FROM system.tables WHERE database = 'clevrr_db' AND name IN ('order_mv_shard', 'order_click_attribution_shard', 'order_mv_shard_legacy', 'order_click_attribution_shard_legacy')
FORMAT Vertical;
SELECT 'order_mv new (FINAL)' AS check, count() AS result FROM clevrr_db.order_mv FINAL FORMAT Vertical;
SELECT 'order_mv old, distinct orders' AS check, uniqExact(organization_id, source_type, order_id) AS result
FROM clusterAllReplicas('clevrr-cluster', clevrr_db.order_mv_shard_legacy) FORMAT Vertical;
SELECT 'order_click_attribution new (FINAL)' AS check, count() AS result FROM clevrr_db.order_click_attribution FINAL FORMAT Vertical;
SELECT 'order_click_attribution old, distinct orders' AS check, uniqExact(organization_id, source_type, order_id) AS result
FROM clusterAllReplicas('clevrr-cluster', clevrr_db.order_click_attribution_shard_legacy) FORMAT Vertical;
SELECT 'ch_migrations' AS check, groupArray(version) AS result FROM clevrr_db.ch_migrations FINAL FORMAT Vertical;
