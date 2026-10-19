-- Legacy connectors/meta_ads/raw_sanitized_ad_geo.sql, ported by rule only: values are bound
-- (%(org)s) instead of pasted text, and a literal percent sign is doubled.
--;;
-- Transform Meta region-broken-down insights into advertising_geo_daily:
-- one row per (org, source_type, campaign, state, day) carrying spend.
--
-- Deliberately NOT merged into `advertising`: that table is keyed one row per
-- ad-day and every SUM(spend) in app/services/metric_config.py reads it, so
-- adding a state dimension there would multiply spend across ~37 region rows.
--
-- Scoped by raw_meta_ad_insights_by_region.loaded_at, NOT by the ETL's injected
-- date_filter window, because the region fetcher runs on its OWN watermark
-- (entity 'ads_region') and so fetches a different — usually much wider — date
-- range than the ads window this phase is handed. Filtering on the ads window
-- would leave every backfilled day sitting in raw, never reaching this table.
-- `loaded_at` is stamped on insert AND on conflict-update by
-- ETL/load_meta_ads_region_raw.py, so "recently loaded" means exactly "rows this
-- job (or a recent one) just fetched", whatever dates they cover. The 7-day
-- lookback lets a few failed nights self-heal; the INSERT is idempotent.
--
-- NO DDL IN THIS FILE — not CREATE TABLE, not CREATE INDEX, not ALTER TABLE.
-- This transform runs once per org inside a 4-way parallel pool and DDL locks
-- deadlock parallel org runs. Schema lives in
-- ETL/migrations/2026-09-08_001_ad_geo_daily.sql.

-- Clear the day range covered by the freshly loaded raw rows, so campaigns or
-- states that stopped reporting disappear instead of lingering. Scoped to
-- meta_ads: the table is shared with any future platform that gains a geo
-- breakdown. When nothing was loaded, both bounds are NULL and this is a no-op
-- rather than a wipe with no replacement.
DELETE FROM advertising_geo_daily
WHERE source_type = 'meta_ads'
  AND organization_id = %(org)s
  AND created_at >= (
    SELECT MIN(date_start::timestamp AT TIME ZONE 'UTC')
    FROM raw_meta_ad_insights_by_region
    WHERE organization_id = %(org)s
      AND loaded_at >= now() - interval '7 days'
  )
  AND created_at <= (
    SELECT MAX(date_start::timestamp AT TIME ZONE 'UTC')
    FROM raw_meta_ad_insights_by_region
    WHERE organization_id = %(org)s
      AND loaded_at >= now() - interval '7 days'
  )
--;;
INSERT INTO advertising_geo_daily (
  organization_id,
  source_type,
  campaign_id,
  campaign_name,
  account_id,
  account_name,
  state,
  region,
  spend,
  impressions,
  clicks,
  created_at,
  last_synced
)
SELECT
  r.organization_id,
  'meta_ads' AS source_type,
  r.campaign_id,
  -- COALESCE to '' throughout: the matching ClickHouse columns are non-nullable
  -- String/LowCardinality(String), so a NULL Meta omitted would fail the Parquet
  -- insert rather than the row simply loading blank.
  COALESCE(MAX(r.campaign_name), '') AS campaign_name,
  COALESCE(MAX(r.account_id), '')    AS account_id,
  COALESCE(MAX(r.account_name), '')  AS account_name,
  r.state,
  -- Several raw regions can canonicalise to one state (Orissa/Odisha,
  -- Pondicherry/Puducherry), which is why this aggregates by state rather than
  -- by region. Keep one raw label for debugging the mapping.
  COALESCE(MAX(r.region), '')        AS region,
  SUM(r.spend)::NUMERIC(14,2) AS spend,
  SUM(r.impressions)::BIGINT  AS impressions,
  SUM(r.clicks)::BIGINT       AS clicks,
  (r.date_start::timestamp AT TIME ZONE 'UTC') AS created_at,
  now() AS last_synced
FROM raw_meta_ad_insights_by_region r
WHERE r.organization_id = %(org)s
  AND r.campaign_id IS NOT NULL
  AND r.state IS NOT NULL
  AND r.loaded_at >= now() - interval '7 days'
GROUP BY
  r.organization_id,
  r.campaign_id,
  r.state,
  (r.date_start::timestamp AT TIME ZONE 'UTC')
ON CONFLICT (organization_id, source_type, created_at, campaign_id, state) DO UPDATE SET
  campaign_name = EXCLUDED.campaign_name,
  account_id    = EXCLUDED.account_id,
  account_name  = EXCLUDED.account_name,
  region        = EXCLUDED.region,
  spend         = EXCLUDED.spend,
  impressions   = EXCLUDED.impressions,
  clicks        = EXCLUDED.clicks,
  last_synced   = EXCLUDED.last_synced
