-- order_mv: replicated, and sharded by the order.
-- Legacy's order_mv_shard is a plain ReplacingMergeTree, so each replica of a shard holds its
-- own rows (sandbox clevrr_db, 2026-10-17: 56,694 vs 51,863 rows on shard 0) and a query reads
-- whichever replica it lands on. Its Distributed table used rand(), so a re-synced order landed
-- on the other shard and showed twice.
-- Keeps the rows: the shard table is renamed to order_mv_shard_legacy, the new tables are created
-- under the old names, and the old rows are copied in from every replica (clusterAllReplicas: an
-- unreplicated shard table holds different rows on each replica; the new key places them and
-- FINAL collapses versions). The Distributed table holds no rows and refers to its shard table
-- by name, so it is dropped and recreated, not renamed (a renamed one reads the new shard table;
-- sandbox 2026-10-19: the copy moved 0 rows that way). Drop order_mv_shard_legacy once the copy
-- is verified.
--;;
-- cluster only
DROP TABLE IF EXISTS ${db}.order_mv ON CLUSTER '${cluster}' SYNC
--;;
RENAME TABLE ${db}.order_mv_shard TO ${db}.order_mv_shard_legacy ON CLUSTER '${cluster}'
--;;
CREATE TABLE IF NOT EXISTS ${db}.order_mv_shard ON CLUSTER '${cluster}'
(
    `organization_id` LowCardinality(String),
    `order_id` LowCardinality(String),
    `order_date` DateTime,
    `created_at` DateTime,
    `source_type` LowCardinality(String),
    `customer_id` LowCardinality(Nullable(String)),
    `customer_name` LowCardinality(Nullable(String)),
    `financial_status` LowCardinality(Nullable(String)),
    `fulfillment_status` LowCardinality(Nullable(String)),
    `cancelled_at` Nullable(DateTime),
    `currency` LowCardinality(Nullable(String)),
    `total_price` Nullable(Float64),
    `shipping_price` Nullable(Float64),
    `tax_price` Nullable(Float64),
    `gross_sales` Nullable(Float64),
    `discounts` Nullable(Float64),
    `returns` Nullable(Float64),
    `net_sales` Nullable(Float64),
    `order_tax` Nullable(Float64),
    `cogs` Nullable(Float64),
    `packaging_cost` Nullable(Float64),
    `platform_fees` Nullable(Float64),
    `txn_fee` Nullable(Float64),
    `contribution_profit` Nullable(Float64),
    `contribution_margin` Nullable(Float64),
    `line_item_count` Nullable(UInt32),
    `quantity_total` Nullable(Int32),
    `updated_at` DateTime DEFAULT now()
)
ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/${db}/order_mv_shard', '{replica}', updated_at)
PARTITION BY toYYYYMM(created_at)
ORDER BY (organization_id, toYYYYMM(created_at), source_type, created_at, order_id)
TTL created_at + toIntervalYear(5)
SETTINGS index_granularity = 8192
--;;
-- cluster only
CREATE TABLE IF NOT EXISTS ${db}.order_mv ON CLUSTER '${cluster}'
AS ${db}.order_mv_shard
ENGINE = Distributed('${cluster}', '${db}', 'order_mv_shard', sipHash64(organization_id, source_type, order_id))
--;;
-- cluster only
INSERT INTO ${db}.order_mv
    (organization_id, order_id, order_date, created_at, source_type, customer_id, customer_name,
     financial_status, fulfillment_status, cancelled_at, currency, total_price, shipping_price,
     tax_price, gross_sales, discounts, returns, net_sales, order_tax, cogs, packaging_cost,
     platform_fees, txn_fee, contribution_profit, contribution_margin, line_item_count,
     quantity_total, updated_at)
SELECT organization_id, order_id, order_date, created_at, source_type, customer_id, customer_name,
       financial_status, fulfillment_status, cancelled_at, currency, total_price, shipping_price,
       tax_price, gross_sales, discounts, returns, net_sales, order_tax, cogs, packaging_cost,
       platform_fees, txn_fee, contribution_profit, contribution_margin, line_item_count,
       quantity_total, updated_at
FROM clusterAllReplicas('${cluster}', ${db}.order_mv_shard_legacy)
SETTINGS insert_distributed_sync = 1
