-- order_click_attribution: sharded by the order (legacy's Distributed table used rand()).
-- It is ReplacingMergeTree, which collapses an order's versions only within one shard, so a
-- re-synced order landed on the other shard and showed twice (sandbox 2026-10-19: 624 rows for
-- 424 orders after one rerun). Keeps the rows: the shard table is kept as
-- order_click_attribution_shard_legacy, a fresh shard table and a Distributed table with the new
-- key are created under the old names, and the old rows are copied from every replica through
-- the new Distributed table, which places them by the new key (FINAL collapses the copies). The
-- Distributed table holds no rows and refers to its shard table by name, so it is dropped and
-- recreated, not renamed (see 0002). Drop order_click_attribution_shard_legacy once the copy is
-- verified.
--;;
-- cluster only
DROP TABLE IF EXISTS ${db}.order_click_attribution ON CLUSTER '${cluster}' SYNC
--;;
RENAME TABLE ${db}.order_click_attribution_shard TO ${db}.order_click_attribution_shard_legacy ON CLUSTER '${cluster}'
--;;
CREATE TABLE IF NOT EXISTS ${db}.order_click_attribution_shard ON CLUSTER '${cluster}'
AS ${db}.order_click_attribution_shard_legacy
ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/${db}/order_click_attribution_shard_v2', '{replica}', updated_at)
PARTITION BY toYYYYMM(created_at)
ORDER BY (organization_id, toYYYYMM(created_at), source_type, created_at, order_id)
TTL created_at + toIntervalYear(5)
SETTINGS index_granularity = 8192
--;;
-- cluster only
CREATE TABLE IF NOT EXISTS ${db}.order_click_attribution ON CLUSTER '${cluster}'
AS ${db}.order_click_attribution_shard
ENGINE = Distributed('${cluster}', '${db}', 'order_click_attribution_shard', sipHash64(organization_id, source_type, order_id))
--;;
-- cluster only
INSERT INTO ${db}.order_click_attribution
SELECT * FROM clusterAllReplicas('${cluster}', ${db}.order_click_attribution_shard_legacy)
SETTINGS insert_distributed_sync = 1
