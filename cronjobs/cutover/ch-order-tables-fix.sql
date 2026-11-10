-- PROD ClickHouse fix for order_mv and order_click_attribution (clickhouse_migrations 0002 + 0003),

-- rendered for clevrr_db / clevrr-cluster. Run as the ClickHouse admin (the `app` user lacks REMOTE),

-- inside one ClickHouse pod: see docs/prod-cutover.md. The old shard tables stay as *_shard_legacy.

CREATE TABLE IF NOT EXISTS clevrr_db.ch_migrations ON CLUSTER 'clevrr-cluster' (version String, applied_at DateTime DEFAULT now()) ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/clevrr_db/ch_migrations', '{replica}') ORDER BY version;

DROP TABLE IF EXISTS clevrr_db.order_mv ON CLUSTER 'clevrr-cluster' SYNC;

RENAME TABLE clevrr_db.order_mv_shard TO clevrr_db.order_mv_shard_legacy ON CLUSTER 'clevrr-cluster';

CREATE TABLE IF NOT EXISTS clevrr_db.order_mv_shard ON CLUSTER 'clevrr-cluster'
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
ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/clevrr_db/order_mv_shard', '{replica}', updated_at)
PARTITION BY toYYYYMM(created_at)
ORDER BY (organization_id, toYYYYMM(created_at), source_type, created_at, order_id)
TTL created_at + toIntervalYear(5)
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS clevrr_db.order_mv ON CLUSTER 'clevrr-cluster'
AS clevrr_db.order_mv_shard
ENGINE = Distributed('clevrr-cluster', 'clevrr_db', 'order_mv_shard', sipHash64(organization_id, source_type, order_id));

INSERT INTO clevrr_db.order_mv
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
FROM clusterAllReplicas('clevrr-cluster', clevrr_db.order_mv_shard_legacy)
SETTINGS insert_distributed_sync = 1;

DROP TABLE IF EXISTS clevrr_db.order_click_attribution ON CLUSTER 'clevrr-cluster' SYNC;

RENAME TABLE clevrr_db.order_click_attribution_shard TO clevrr_db.order_click_attribution_shard_legacy ON CLUSTER 'clevrr-cluster';

CREATE TABLE IF NOT EXISTS clevrr_db.order_click_attribution_shard ON CLUSTER 'clevrr-cluster'
AS clevrr_db.order_click_attribution_shard_legacy
ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/clevrr_db/order_click_attribution_shard_v2', '{replica}', updated_at)
PARTITION BY toYYYYMM(created_at)
ORDER BY (organization_id, toYYYYMM(created_at), source_type, created_at, order_id)
TTL created_at + toIntervalYear(5)
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS clevrr_db.order_click_attribution ON CLUSTER 'clevrr-cluster'
AS clevrr_db.order_click_attribution_shard
ENGINE = Distributed('clevrr-cluster', 'clevrr_db', 'order_click_attribution_shard', sipHash64(organization_id, source_type, order_id));

INSERT INTO clevrr_db.order_click_attribution
SELECT * FROM clusterAllReplicas('clevrr-cluster', clevrr_db.order_click_attribution_shard_legacy)
SETTINGS insert_distributed_sync = 1;

INSERT INTO clevrr_db.ch_migrations (version) VALUES ('0001'), ('0002'), ('0003');
