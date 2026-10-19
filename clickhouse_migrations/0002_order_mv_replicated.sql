-- order_mv_shard becomes ReplicatedReplacingMergeTree.
-- The baseline copied legacy's plain ReplacingMergeTree, so each replica of a shard holds its
-- own rows (sandbox clevrr_db, 2026-10-17: 56,694 vs 51,863 rows on shard 0), and a query
-- reads whichever replica it lands on: order_mv metrics changed between refreshes.
-- order_mv is derived (shopify-sales rebuilds it from orders, order_lines, refunds and the cost
-- tables), so the old table is dropped, not copied. Where order_mv held data, run a
-- shopify-sales backfill after this migration.
--;;
DROP TABLE IF EXISTS ${db}.order_mv_shard ON CLUSTER '${cluster}' SYNC
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
