-- Legacy connectors/meta_ads/raw_sanitized_campaign_structure.sql, ported by rule only: values are bound
-- (%(org)s) instead of pasted text, and a literal percent sign is doubled.
--;;
-- Transform Meta object-graph raw tables (raw_meta_campaigns / raw_meta_ad_sets /
-- raw_meta_ads) into the unified campaign_structure NODE table: one row per entity
-- (campaign OR ad_set OR ad), delivery-INDEPENDENT so paused entities are included.
--
-- Budget note: Meta returns daily_budget / lifetime_budget in the account currency's
-- MINOR unit (e.g. paise / cents), so we divide by 100 to get major-unit amounts.
-- CBO (campaign budget optimization) is inferred from WHERE the budget lives: a budget
-- on the campaign => is_cbo; a budget on the ad set => ABO.
--
-- The `campaign_structure` table (and its raw source tables) are created out-of-band
-- (see the DDL run against Postgres), like `advertising`. This file is a pure
-- transform: full-refresh DELETE for the org, then INSERT the current snapshot.

-- Full refresh for this org's Meta structure (raw tables are a current snapshot).
DELETE FROM campaign_structure
WHERE source_type = 'meta_ads' AND organization_id = %(org)s
--;;
WITH acct AS (
  SELECT DISTINCT account_id, account_name
  FROM   advertising
  WHERE  source_type = 'meta_ads' AND account_name IS NOT NULL
),

campaigns AS (
  SELECT
    gen_random_uuid()                                   AS id,
    c.organization_id,
    'meta_ads'                                          AS source_type,
    'campaign'                                          AS entity_level,
    c.campaign_id                                       AS entity_id,
    c.name                                              AS entity_name,
    NULL::TEXT                                          AS parent_id,
    c.campaign_id                                       AS campaign_id,
    NULL::TEXT                                          AS adset_id,
    c.status,
    c.effective_status,
    c.objective,
    (COALESCE(NULLIF(c.daily_budget, '')::NUMERIC,
              NULLIF(c.lifetime_budget, '')::NUMERIC) / 100.0)::NUMERIC(18,2) AS budget_amount,
    CASE WHEN NULLIF(c.daily_budget, '')    IS NOT NULL THEN 'daily'
         WHEN NULLIF(c.lifetime_budget, '') IS NOT NULL THEN 'lifetime' END   AS budget_type,
    CASE WHEN NULLIF(c.daily_budget, '') IS NOT NULL
           OR NULLIF(c.lifetime_budget, '') IS NOT NULL THEN 1 ELSE 0 END     AS is_cbo,
    NULL::TEXT                                          AS optimization_goal,
    NULL::TEXT                                          AS billing_event,
    c.bid_strategy,
    NULLIF(c.start_time, '')                            AS start_time,
    NULLIF(c.stop_time, '')                             AS stop_time,
    NULLIF(c.updated_time, '')                          AS last_edited,
    NULL::TEXT                                          AS learning_stage_status,
    NULL::TEXT                                          AS learning_last_sig_edit,
    c.account_id,
    a.account_name,
    NOW()                                               AS last_synced
  FROM   raw_meta_campaigns c
  LEFT   JOIN acct a ON a.account_id = c.account_id
  WHERE  c.organization_id = %(org)s
),

adsets AS (
  SELECT
    gen_random_uuid()                                   AS id,
    s.organization_id,
    'meta_ads'                                          AS source_type,
    'ad_set'                                            AS entity_level,
    s.adset_id                                          AS entity_id,
    s.name                                              AS entity_name,
    s.campaign_id                                       AS parent_id,
    s.campaign_id                                       AS campaign_id,
    s.adset_id                                          AS adset_id,
    s.status,
    s.effective_status,
    NULL::TEXT                                          AS objective,
    (COALESCE(NULLIF(s.daily_budget, '')::NUMERIC,
              NULLIF(s.lifetime_budget, '')::NUMERIC) / 100.0)::NUMERIC(18,2) AS budget_amount,
    CASE WHEN NULLIF(s.daily_budget, '')    IS NOT NULL THEN 'daily'
         WHEN NULLIF(s.lifetime_budget, '') IS NOT NULL THEN 'lifetime' END   AS budget_type,
    -- is_cbo reflects the PARENT campaign: does the campaign hold the budget?
    CASE WHEN NULLIF(pc.daily_budget, '') IS NOT NULL
           OR NULLIF(pc.lifetime_budget, '') IS NOT NULL THEN 1 ELSE 0 END    AS is_cbo,
    s.optimization_goal,
    s.billing_event,
    s.bid_strategy,
    NULLIF(s.start_time, '')                            AS start_time,
    NULLIF(s.end_time, '')                              AS stop_time,
    NULLIF(s.updated_time, '')                          AS last_edited,
    NULLIF(s.learning_stage_status, '')                 AS learning_stage_status,
    NULLIF(s.learning_last_sig_edit_ts, '')             AS learning_last_sig_edit,
    s.account_id,
    a.account_name,
    NOW()                                               AS last_synced
  FROM   raw_meta_ad_sets s
  LEFT   JOIN raw_meta_campaigns pc
         ON pc.organization_id = s.organization_id AND pc.campaign_id = s.campaign_id
  LEFT   JOIN acct a ON a.account_id = s.account_id
  WHERE  s.organization_id = %(org)s
),

ads AS (
  SELECT
    gen_random_uuid()                                   AS id,
    d.organization_id,
    'meta_ads'                                          AS source_type,
    'ad'                                                AS entity_level,
    d.ad_id                                             AS entity_id,
    d.name                                              AS entity_name,
    d.adset_id                                          AS parent_id,
    d.campaign_id                                       AS campaign_id,
    d.adset_id                                          AS adset_id,
    d.status,
    d.effective_status,
    NULL::TEXT                                          AS objective,
    NULL::NUMERIC(18,2)                                 AS budget_amount,
    NULL::TEXT                                          AS budget_type,
    CASE WHEN NULLIF(pc.daily_budget, '') IS NOT NULL
           OR NULLIF(pc.lifetime_budget, '') IS NOT NULL THEN 1 ELSE 0 END    AS is_cbo,
    NULL::TEXT                                          AS optimization_goal,
    NULL::TEXT                                          AS billing_event,
    NULL::TEXT                                          AS bid_strategy,
    NULL::TEXT                                          AS start_time,
    NULL::TEXT                                          AS stop_time,
    NULLIF(d.updated_time, '')                          AS last_edited,
    NULL::TEXT                                          AS learning_stage_status,
    NULL::TEXT                                          AS learning_last_sig_edit,
    d.account_id,
    a.account_name,
    NOW()                                               AS last_synced
  FROM   raw_meta_ads d
  LEFT   JOIN raw_meta_campaigns pc
         ON pc.organization_id = d.organization_id AND pc.campaign_id = d.campaign_id
  LEFT   JOIN acct a ON a.account_id = d.account_id
  WHERE  d.organization_id = %(org)s
)

INSERT INTO campaign_structure (
  id, organization_id, source_type, entity_level, entity_id, entity_name,
  parent_id, campaign_id, adset_id, status, effective_status, objective,
  budget_amount, budget_type, is_cbo, optimization_goal, billing_event,
  bid_strategy, start_time, stop_time, last_edited, learning_stage_status,
  learning_last_sig_edit, account_id, account_name, last_synced
)
SELECT * FROM campaigns
UNION ALL
SELECT * FROM adsets
UNION ALL
SELECT * FROM ads
