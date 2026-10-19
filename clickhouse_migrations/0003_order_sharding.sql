-- Shard order_click_attribution and order_mv by the order, as orders is sharded.
-- Both are ReplacingMergeTree, which collapses an order's versions only within one shard, but
-- legacy's Distributed tables sent each insert to a random shard (rand()). A re-synced order
-- then landed on the other shard and showed twice (sandbox 2026-10-19: order_click_attribution
-- 624 rows for 424 orders after one incremental rerun). The Distributed tables hold no data:
-- dropping and creating them loses nothing. Rows already placed by rand() stay where they are,
-- so clear the two _shard tables and re-sync (a shopify-sales backfill) where they held data.
--;;
-- cluster only
DROP TABLE IF EXISTS ${db}.order_click_attribution ON CLUSTER '${cluster}' SYNC
--;;
CREATE TABLE IF NOT EXISTS ${db}.order_click_attribution ON CLUSTER '${cluster}'
(
    `organization_id` LowCardinality(String),
    `order_id` String,
    `source_type` LowCardinality(String),
    `landing_url` Nullable(String),
    `landing_url_source` LowCardinality(Nullable(String)),
    `gclid` Nullable(String),
    `gbraid` Nullable(String),
    `wbraid` Nullable(String),
    `fbclid` Nullable(String),
    `srsltid` Nullable(String),
    `gad_source` LowCardinality(Nullable(String)),
    `gad_campaignid` Nullable(String),
    `utm_source` LowCardinality(Nullable(String)),
    `utm_medium` LowCardinality(Nullable(String)),
    `utm_campaign` LowCardinality(Nullable(String)),
    `utm_term` Nullable(String),
    `utm_content` Nullable(String),
    `utm_id` Nullable(String),
    `landing_product_handle` Nullable(String),
    `landing_variant_id` Nullable(UInt64),
    `product_id` Nullable(UInt64),
    `product_name` Nullable(String),
    `campaign_id` Nullable(String),
    `campaign_name` Nullable(String),
    `is_paid_google_click` UInt8 DEFAULT 0,
    `ad_id` Nullable(String),
    `ad_platform` LowCardinality(Nullable(String)),
    `ad_id_source` LowCardinality(Nullable(String)),
    `is_paid_meta_click` UInt8 DEFAULT 0,
    `order_date` DateTime CODEC(Delta(4), LZ4),
    `created_at` DateTime CODEC(Delta(4), LZ4),
    `updated_at` DateTime CODEC(Delta(4), LZ4),
    `last_synced` DateTime DEFAULT now() CODEC(Delta(4), LZ4)
)
ENGINE = Distributed('${cluster}', '${db}', 'order_click_attribution_shard', sipHash64(organization_id, source_type, order_id))
--;;
-- cluster only
DROP TABLE IF EXISTS ${db}.order_mv ON CLUSTER '${cluster}' SYNC
--;;
CREATE TABLE IF NOT EXISTS ${db}.order_mv ON CLUSTER '${cluster}'
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
ENGINE = Distributed('${cluster}', '${db}', 'order_mv_shard', sipHash64(organization_id, source_type, order_id))
