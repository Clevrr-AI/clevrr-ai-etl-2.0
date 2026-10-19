-- order_click_attribution for one org (legacy connectors/shopify/raw_sanitized_order_click_attribution.sql).
-- Ported by rule: values are bound (%(org)s, %(exclude_pos)s, %(since)s,
-- %(created_from)s, %(created_to)s) instead of pasted ${...} text, a literal percent sign
-- is doubled, and the app's tables are read from public.
-- Also dropped: legacy's own CREATE TABLE IF NOT EXISTS (the table comes from
-- Alembic; a step never runs DDL).
-- The window: orders updated since %(since)s (an incremental run) or created in
-- [%(created_from)s, %(created_to)s) (a backfill); legacy ran each file twice, on created_at
-- and then on updated_at.
--;;
-- Per-order click attribution: parse the landing URL into its click identifiers, UTM
-- params and landing product.
--
-- WHY THIS EXISTS
-- raw_shopify_orders.landing_site already carries everything needed to tie an order to a
-- Google campaign and a product, but raw_sanitized_orders.sql collapses it into a single
-- `sale_initiated_category` bucket and discards the rest. Two things are lost:
--
--   1. `gad_campaignid` — Google appends this to auto-tagged clicks and it is verbatim
--      `advertising.campaign_id`. A deterministic order -> campaign link with no UTM
--      guesswork, finer than the platform-level PAID-GOOGLE bucket.
--   2. The `/products/{handle}?variant={id}` path — identifies the product the paid click
--      actually landed on. This is the only product signal available for Performance Max
--      and Shopping campaigns, which have no ads and therefore no rows in
--      ad_product_mapping.
--
-- DELIBERATELY NOT A COLUMN ON `orders`: that table and its ClickHouse mirror are the
-- hottest in the warehouse; these fields are read only by attribution queries.
--
-- RELATIONSHIP TO raw_sanitized_orders.sql: that file keeps its own `attrs` CTE, which
-- needs explicit-note-attribute vs in-URL variants of each UTM to drive its classification
-- branches. This transform only needs the raw parameter values, so it unifies every
-- candidate URL into one relation and runs a single pass of extractions over it. It is not
-- a third copy of that branch logic and must not grow into one.
--


-- NO ALTER TABLE and NO CREATE INDEX here, on purpose. ALTER TABLE takes an
-- AccessExclusiveLock before it evaluates IF NOT EXISTS, and CREATE INDEX IF NOT EXISTS
-- takes a ShareLock on the table even when the index already exists — the nightly runs orgs
-- in parallel on this same table, and both lock types deadlocked those runs (8 orgs on
-- 2026-08-27, and nightly through 2026-08-28). Schema changes go through ETL/migrations/
-- (columns: 2026-08-27_order_attributed_ad_product_mapping.sql; indexes:
-- 2026-08-31_006_order_click_attribution.sql, applied by utils/migrations_runner.py).
-- Only the CREATE TABLE bootstrap above is genuinely lock-free when the table exists.

-- Org filter policy (2026-09-21): the WHERE clauses below compare the raw row's own
-- organization_id directly. Wrapping the column in COALESCE over a LEFT JOIN made the
-- predicate non-sargable (no index, no per-org selectivity), which fed the rows=1
-- misestimate behind the 2026-09-18..20 nightly failures.
-- 2026-10-07: the Hevo-era account-to-org fallback join was removed. Every raw row now
-- carries its own organization_id, so no lookup table is consulted here any more.
WITH
-- Same windowing contract as raw_sanitized_orders.sql: date-window the orders once, then
-- scope the (undated) note-attribute scan to those order_ids.
windowed_orders AS (
  SELECT
    ro.id AS order_id,
    ro.organization_id,
    ro.landing_site,
    (ro.processed_at AT TIME ZONE 'Asia/Kolkata') AS order_date,
    (ro.created_at   AT TIME ZONE 'Asia/Kolkata') AS created_at,
    (ro.updated_at   AT TIME ZONE 'Asia/Kolkata') AS updated_at
  FROM raw_shopify_orders ro
  -- Hevo is gone; the in-house fetchers leave __hevo__marked_deleted NULL, so the old
  -- `= false` predicate silently dropped every row (all 887 shopbuilt orders were NULL).
  WHERE COALESCE(ro.__hevo__marked_deleted, false) = false
    AND ro.organization_id IS NOT NULL
    AND ro.organization_id = %(org)s
    AND (NOT %(exclude_pos)s OR upper(coalesce(ro.source_name, '')) <> 'POS')
    AND (ro.updated_at >= %(since)s OR (ro.created_at >= %(created_from)s AND ro.created_at < %(created_to)s))
),

-- Every URL-shaped string that might carry the click params, with a preference rank.
-- landing_site (pref 1) is Shopify's own record of the first page hit and always wins.
-- Headless checkouts (GoKwik / Breeze / Shopflo / Snapmint) leave landing_site NULL and
-- stash the URL in a note attribute instead (pref 2).
url_candidates AS (
  SELECT w.order_id, w.organization_id, 1 AS pref, w.landing_site AS url
  FROM windowed_orders w
  WHERE NULLIF(btrim(w.landing_site), '') IS NOT NULL

  UNION ALL

  SELECT w.order_id, w.organization_id, 2 AS pref, na.value AS url
  FROM raw_shopify_order_note_attributes na
  JOIN windowed_orders w ON w.order_id = na.order_id
  WHERE NULLIF(btrim(na.value), '') IS NOT NULL
    AND na.value LIKE '%%=%%'
    AND (
      na.value ILIKE '%%utm_%%'      OR na.value ILIKE '%%gclid%%'      OR
      na.value ILIKE '%%gbraid%%'    OR na.value ILIKE '%%wbraid%%'     OR
      na.value ILIKE '%%gad_%%'      OR na.value ILIKE '%%srsltid%%'    OR
      na.value ILIKE '%%fbclid%%'    OR na.value ILIKE '%%/products/%%' OR
      na.value ILIKE '%%/collections/%%'
    )
),

-- One regex pass per parameter. The `(?:[?&]|^)` prefix mirrors the idiom already used in
-- raw_sanitized_orders.sql so a bare query string (no leading path) still matches.
extracted AS (
  SELECT
    uc.order_id,
    uc.organization_id,
    uc.pref,
    uc.url,
    (regexp_match(uc.url, '(?i)(?:[?&]|^)gclid=([^&#[:space:]]+)'))[1]          AS gclid,
    (regexp_match(uc.url, '(?i)(?:[?&]|^)gbraid=([^&#[:space:]]+)'))[1]         AS gbraid,
    (regexp_match(uc.url, '(?i)(?:[?&]|^)wbraid=([^&#[:space:]]+)'))[1]         AS wbraid,
    (regexp_match(uc.url, '(?i)(?:[?&]|^)fbclid=([^&#[:space:]]+)'))[1]         AS fbclid,
    (regexp_match(uc.url, '(?i)(?:[?&]|^)srsltid=([^&#[:space:]]+)'))[1]        AS srsltid,
    (regexp_match(uc.url, '(?i)(?:[?&]|^)gad_source=([^&#[:space:]]+)'))[1]     AS gad_source,
    (regexp_match(uc.url, '(?i)(?:[?&]|^)gad_campaignid=([^&#[:space:]]+)'))[1] AS gad_campaignid,
    (regexp_match(uc.url, '(?i)(?:[?&]|^)utm_source=([^&#[:space:]]+)'))[1]     AS utm_source,
    (regexp_match(uc.url, '(?i)(?:[?&]|^)utm_medium=([^&#[:space:]]+)'))[1]     AS utm_medium,
    (regexp_match(uc.url, '(?i)(?:[?&]|^)utm_campaign=([^&#[:space:]]+)'))[1]   AS utm_campaign,
    (regexp_match(uc.url, '(?i)(?:[?&]|^)utm_term=([^&#[:space:]]+)'))[1]       AS utm_term,
    (regexp_match(uc.url, '(?i)(?:[?&]|^)utm_content=([^&#[:space:]]+)'))[1]    AS utm_content,
    (regexp_match(uc.url, '(?i)(?:[?&]|^)utm_id=([^&#[:space:]]+)'))[1]         AS utm_id,
    -- Meta's explicit params (present when "Add URL parameters" is on in Ads Manager).
    -- Named meta_* here because `campaign_id` is the resolved output column below.
    (regexp_match(uc.url, '(?i)(?:[?&]|^)ad_id=([0-9]+)'))[1]                   AS meta_ad_id_param,
    (regexp_match(uc.url, '(?i)(?:[?&]|^)campaign_id=([0-9]+)'))[1]             AS meta_campaign_id_param,
    (regexp_match(uc.url, '(?i)(?:[?&]|^)variant=([0-9]+)'))[1]                 AS landing_variant_id,
    -- /products/{handle} — strip the query string, fragment and any trailing path segment.
    CASE WHEN uc.url LIKE '%%/products/%%' THEN
      NULLIF(
        split_part(
          split_part(
            split_part(split_part(uc.url, '/products/', 2), '?', 1),
            '#', 1
          ),
          '/', 1
        ),
        ''
      )
    END AS landing_product_handle
  FROM url_candidates uc
),

-- Collapse to one row per order, taking each parameter from the most-preferred candidate
-- that actually carried it. A single order can have landing_site with the UTMs and a note
-- attribute with the gclid, so parameters are resolved independently rather than picking
-- one winning URL and reading everything off it.
params AS (
  SELECT
    e.order_id,
    e.organization_id,
    (array_agg(e.url                    ORDER BY e.pref))[1]                                                AS landing_url,
    (array_agg(e.pref                   ORDER BY e.pref))[1]                                                AS landing_url_pref,
    (array_agg(e.gclid                  ORDER BY e.pref) FILTER (WHERE e.gclid                  IS NOT NULL))[1] AS gclid,
    (array_agg(e.gbraid                 ORDER BY e.pref) FILTER (WHERE e.gbraid                 IS NOT NULL))[1] AS gbraid,
    (array_agg(e.wbraid                 ORDER BY e.pref) FILTER (WHERE e.wbraid                 IS NOT NULL))[1] AS wbraid,
    (array_agg(e.fbclid                 ORDER BY e.pref) FILTER (WHERE e.fbclid                 IS NOT NULL))[1] AS fbclid,
    (array_agg(e.srsltid                ORDER BY e.pref) FILTER (WHERE e.srsltid                IS NOT NULL))[1] AS srsltid,
    (array_agg(e.gad_source             ORDER BY e.pref) FILTER (WHERE e.gad_source             IS NOT NULL))[1] AS gad_source,
    (array_agg(e.gad_campaignid         ORDER BY e.pref) FILTER (WHERE e.gad_campaignid         IS NOT NULL))[1] AS gad_campaignid,
    (array_agg(e.utm_source             ORDER BY e.pref) FILTER (WHERE e.utm_source             IS NOT NULL))[1] AS utm_source,
    (array_agg(e.utm_medium             ORDER BY e.pref) FILTER (WHERE e.utm_medium             IS NOT NULL))[1] AS utm_medium,
    (array_agg(e.utm_campaign           ORDER BY e.pref) FILTER (WHERE e.utm_campaign           IS NOT NULL))[1] AS utm_campaign,
    (array_agg(e.utm_term               ORDER BY e.pref) FILTER (WHERE e.utm_term               IS NOT NULL))[1] AS utm_term,
    (array_agg(e.utm_content            ORDER BY e.pref) FILTER (WHERE e.utm_content            IS NOT NULL))[1] AS utm_content,
    (array_agg(e.utm_id                 ORDER BY e.pref) FILTER (WHERE e.utm_id                 IS NOT NULL))[1] AS utm_id,
    (array_agg(e.meta_ad_id_param       ORDER BY e.pref) FILTER (WHERE e.meta_ad_id_param       IS NOT NULL))[1] AS meta_ad_id_param,
    (array_agg(e.meta_campaign_id_param ORDER BY e.pref) FILTER (WHERE e.meta_campaign_id_param IS NOT NULL))[1] AS meta_campaign_id_param,
    (array_agg(e.landing_variant_id     ORDER BY e.pref) FILTER (WHERE e.landing_variant_id     IS NOT NULL))[1] AS landing_variant_id,
    (array_agg(e.landing_product_handle ORDER BY e.pref) FILTER (WHERE e.landing_product_handle IS NOT NULL))[1] AS landing_product_handle
  FROM extracted e
  GROUP BY e.order_id, e.organization_id
),

-- Campaign ids present in `advertising`, so an unrecognised id is stored as NULL rather
-- than as a dangling reference. Mirrors _fetch_campaign_objectives in
-- app/services/attribution/pixel_attribution_job.py.
google_campaigns AS (
  SELECT organization_id, campaign_id::TEXT AS campaign_id, MAX(campaign_name) AS campaign_name
  FROM advertising
  WHERE source_type = 'google_ads'
    AND campaign_id IS NOT NULL
    AND organization_id = %(org)s
  GROUP BY organization_id, campaign_id
),

-- Meta ads we actually hold insights for. A number in the URL only counts as an ad id if
-- it is in this list; campaign/adset come from here, never from the URL.
meta_ads AS (
  SELECT
    organization_id,
    ad_id::TEXT AS ad_id,
    MAX(ad_name) AS ad_name,
    MAX(campaign_id::TEXT) AS campaign_id,
    MAX(campaign_name) AS campaign_name
  FROM advertising
  WHERE source_type = 'meta_ads'
    AND ad_id IS NOT NULL
    AND organization_id = %(org)s
  GROUP BY organization_id, ad_id
),

-- Meta ad-id resolution. Which URL param carries {{ad.id}} depends on the advertiser's
-- URL-parameter template (raw_meta_ad_creative.url_tags): shopbuilt alone uses five, with
-- the ad id in utm_content for some creatives and utm_campaign for others. So this is a
-- priority ladder, each rung validated against meta_ads:
--   1. explicit ad_id= param            (Meta appends it when "Add URL parameters" is on)
--   2. utm_content  if it is a known ad id
--   3. utm_campaign if it is a known ad id
--   4. utm_medium   if it is a known ad id
--   5. utm_term     if it is a known ad id
-- Nothing matched: ad_id stays NULL; fbclid alone still flags is_paid_meta_click.
meta_resolved AS (
  SELECT
    p.order_id,
    p.organization_id,
    COALESCE(m1.ad_id, m2.ad_id, m3.ad_id, m4.ad_id, m5.ad_id) AS meta_ad_id,
    CASE
      WHEN m1.ad_id IS NOT NULL THEN 'ad_id_param'
      WHEN m2.ad_id IS NOT NULL THEN 'utm_content'
      WHEN m3.ad_id IS NOT NULL THEN 'utm_campaign'
      WHEN m4.ad_id IS NOT NULL THEN 'utm_medium'
      WHEN m5.ad_id IS NOT NULL THEN 'utm_term'
    END AS meta_ad_id_source,
    COALESCE(m1.ad_name, m2.ad_name, m3.ad_name, m4.ad_name, m5.ad_name) AS meta_ad_name,
    COALESCE(m1.campaign_id, m2.campaign_id, m3.campaign_id, m4.campaign_id, m5.campaign_id) AS meta_campaign_id,
    COALESCE(m1.campaign_name, m2.campaign_name, m3.campaign_name, m4.campaign_name, m5.campaign_name) AS meta_campaign_name
  FROM params p
  LEFT JOIN meta_ads m1 ON m1.organization_id = p.organization_id AND m1.ad_id = p.meta_ad_id_param
  LEFT JOIN meta_ads m2 ON m2.organization_id = p.organization_id AND p.utm_content  ~ '^[0-9]{15,20}$' AND m2.ad_id = p.utm_content
  LEFT JOIN meta_ads m3 ON m3.organization_id = p.organization_id AND p.utm_campaign ~ '^[0-9]{15,20}$' AND m3.ad_id = p.utm_campaign
  LEFT JOIN meta_ads m4 ON m4.organization_id = p.organization_id AND p.utm_medium   ~ '^[0-9]{15,20}$' AND m4.ad_id = p.utm_medium
  LEFT JOIN meta_ads m5 ON m5.organization_id = p.organization_id AND p.utm_term     ~ '^[0-9]{15,20}$' AND m5.ad_id = p.utm_term
),

resolved AS (
  SELECT
    w.organization_id,
    w.order_id,
    'shopify'::VARCHAR(100) AS source_type,
    p.landing_url,
    CASE WHEN p.landing_url_pref = 1 THEN 'landing_site' ELSE 'note_attributes' END AS landing_url_source,
    p.gclid, p.gbraid, p.wbraid, p.fbclid, p.srsltid, p.gad_source, p.gad_campaignid,
    p.utm_source, p.utm_medium, p.utm_campaign, p.utm_term, p.utm_content, p.utm_id,
    p.landing_product_handle,
    p.landing_variant_id::BIGINT AS landing_variant_id,
    -- Variant id first: it names the exact variant, so it survives handle renames and
    -- resolves correctly when the URL path is a collection route. Handle is the fallback.
    COALESCE(pv.product_id, ph.product_id) AS product_id,
    COALESCE(pvp.title, ph.title)          AS product_name,
    -- Platform-neutral campaign: Google wins when a Google click id resolved (a single
    -- click cannot be both), else the Meta campaign looked up from the resolved ad id.
    COALESCE(gc.campaign_id,   mr.meta_campaign_id)   AS campaign_id,
    COALESCE(gc.campaign_name, mr.meta_campaign_name) AS campaign_name,
    (
      p.gclid IS NOT NULL OR p.gbraid IS NOT NULL
      OR p.wbraid IS NOT NULL OR p.gad_source IS NOT NULL
    ) AS is_paid_google_click,
    mr.meta_ad_id AS ad_id,
    CASE
      WHEN gc.campaign_id IS NOT NULL THEN 'google'
      WHEN mr.meta_ad_id IS NOT NULL THEN 'meta'
    END AS ad_platform,
    mr.meta_ad_id_source AS ad_id_source,
    (p.fbclid IS NOT NULL OR mr.meta_ad_id IS NOT NULL) AS is_paid_meta_click,
    w.order_date,
    w.created_at,
    w.updated_at
  FROM windowed_orders w
  JOIN params p
    ON p.order_id = w.order_id
    AND p.organization_id = w.organization_id
  LEFT JOIN meta_resolved mr
    ON mr.order_id = w.order_id
    AND mr.organization_id = w.organization_id
  LEFT JOIN product_variants pv
    ON pv.organization_id = w.organization_id
    AND pv.variant_id = p.landing_variant_id::BIGINT
  LEFT JOIN products pvp
    ON pvp.organization_id = pv.organization_id
    AND pvp.product_id = pv.product_id
  LEFT JOIN products ph
    ON ph.organization_id = w.organization_id
    AND ph.handle = p.landing_product_handle
  -- gad_campaignid is Google's own id and wins; utm_id is the GA4 campaign id a manual
  -- {campaignid} template would have written, used only when auto-tagging is absent.
  LEFT JOIN google_campaigns gc
    ON gc.organization_id = w.organization_id
    AND gc.campaign_id = COALESCE(p.gad_campaignid, p.utm_id)
)

INSERT INTO order_click_attribution (
  organization_id, order_id, source_type,
  landing_url, landing_url_source,
  gclid, gbraid, wbraid, fbclid, srsltid, gad_source, gad_campaignid,
  utm_source, utm_medium, utm_campaign, utm_term, utm_content, utm_id,
  landing_product_handle, landing_variant_id, product_id, product_name,
  campaign_id, campaign_name, is_paid_google_click,
  ad_id, ad_platform, ad_id_source, is_paid_meta_click,
  order_date, created_at, updated_at
)
SELECT
  organization_id, order_id, source_type,
  landing_url, landing_url_source,
  gclid, gbraid, wbraid, fbclid, srsltid, gad_source, gad_campaignid,
  utm_source, utm_medium, utm_campaign, utm_term, utm_content, utm_id,
  landing_product_handle, landing_variant_id, product_id, product_name,
  campaign_id, campaign_name, is_paid_google_click,
  ad_id, ad_platform, ad_id_source, is_paid_meta_click,
  order_date, created_at, updated_at
FROM resolved
ON CONFLICT (order_id, organization_id)
DO UPDATE SET
  source_type            = EXCLUDED.source_type,
  landing_url            = EXCLUDED.landing_url,
  landing_url_source     = EXCLUDED.landing_url_source,
  gclid                  = EXCLUDED.gclid,
  gbraid                 = EXCLUDED.gbraid,
  wbraid                 = EXCLUDED.wbraid,
  fbclid                 = EXCLUDED.fbclid,
  srsltid                = EXCLUDED.srsltid,
  gad_source             = EXCLUDED.gad_source,
  gad_campaignid         = EXCLUDED.gad_campaignid,
  utm_source             = EXCLUDED.utm_source,
  utm_medium             = EXCLUDED.utm_medium,
  utm_campaign           = EXCLUDED.utm_campaign,
  utm_term               = EXCLUDED.utm_term,
  utm_content            = EXCLUDED.utm_content,
  utm_id                 = EXCLUDED.utm_id,
  landing_product_handle = EXCLUDED.landing_product_handle,
  landing_variant_id     = EXCLUDED.landing_variant_id,
  product_id             = EXCLUDED.product_id,
  product_name           = EXCLUDED.product_name,
  campaign_id            = EXCLUDED.campaign_id,
  campaign_name          = EXCLUDED.campaign_name,
  is_paid_google_click   = EXCLUDED.is_paid_google_click,
  ad_id                  = EXCLUDED.ad_id,
  ad_platform            = EXCLUDED.ad_platform,
  ad_id_source           = EXCLUDED.ad_id_source,
  is_paid_meta_click     = EXCLUDED.is_paid_meta_click,
  order_date             = EXCLUDED.order_date,
  created_at             = EXCLUDED.created_at,
  updated_at             = EXCLUDED.updated_at,
  last_synced            = CURRENT_TIMESTAMP
