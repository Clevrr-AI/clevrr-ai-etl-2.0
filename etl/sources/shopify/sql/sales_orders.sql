-- orders for one org (legacy connectors/shopify/raw_sanitized_orders.sql).
-- Ported by rule: values are bound (%(org)s, %(exclude_pos)s, %(since)s,
-- %(created_from)s, %(created_to)s) instead of pasted ${...} text, a literal percent sign
-- is doubled, and the app's tables are read from public.
-- The window: orders updated since %(since)s (an incremental run) or created in
-- [%(created_from)s, %(created_to)s) (a backfill); legacy ran each file twice, on created_at
-- and then on updated_at.
--;;
INSERT INTO orders (
  order_id,
  order_name,
  source_type,
  organization_id,
  order_date,
  customer_id,
  subtotal_price,
  total_discounts,
  shipping_price,
  tax_price,
  total_price,
  currency,
  financial_status,
  fulfillment_status,
  shipping_carrier,
  shipment_status,
  cancelled_at,
  cancel_reason,
  created_at,
  updated_at,
  city,
  sale_initiated_category,
  discount_code,
  state,
  zip,
  product_full_url,
  customer_name,
  order_status_url,
  phone,
  email,
  tracking_number,
  cart_token,
  utm_source_raw,
  utm_medium_raw,
  utm_campaign_raw,
  utm_content_raw,
  mapped_source_category
)
-- Custom UTM mapping (Settings › UTM Mapping). The query below is the original,
-- unchanged transform wrapped as a subquery; the wrapper adds one column:
--   mapped_source_category = the merchant's layer, else sale_initiated_category unchanged:
--     1. the winning custom AND/OR rule (utm_rules — the scalar subquery below: the
--        matching block with the most conditions, then the newest rule), else
--     2. the plain utm_source mapping (utm_mappings, key utm_source), else
--     3. sale_initiated_category — the built-in R1–R23 verdict.
-- Precedence and operators are defined in app/services/utm_rules.py; the subquery is
-- GENERATED there (pg_rule_lookup_sql('src')) and tests/test_utm_rules.py fails if this
-- copy drifts. Never hand-edit it.
-- An org with no rules therefore gets an exact copy of sale_initiated_category, and
-- the delicate CASE that computes sale_initiated_category is not touched. The raw
-- utm_*_raw columns are emitted by the inner query and never rewritten by a rule, so a
-- rule added or deleted later still re-resolves history. Plain utm_medium /
-- utm_campaign / utm_content mappings are not consulted (decision 2026-09-23). Match key:
-- see normalize_value() in services/utm_normalize.py, which must stay identical to the
-- utm_*_raw expressions at the end of the inner SELECT.
SELECT
  src.*,
  COALESCE(
    -- UTM_RULES_BEGIN (generated: services/utm_rules.pg_rule_lookup_sql('src'))
    (
    SELECT r.mapped_value
    FROM public.utm_rules r
    CROSS JOIN LATERAL jsonb_array_elements(r.blocks) AS b(block)
    WHERE r.organization_id = src.organization_id
      AND jsonb_array_length(b.block) > 0
      AND NOT EXISTS (
        SELECT 1
        FROM jsonb_array_elements(b.block) AS c(cond)
        WHERE NOT COALESCE(
          c.cond->'values' @> to_jsonb(CASE c.cond->>'field'
                                         WHEN 'src' THEN src.utm_source_raw
                                         WHEN 'med' THEN src.utm_medium_raw
                                         WHEN 'cmp' THEN src.utm_campaign_raw
                                         WHEN 'cnt' THEN src.utm_content_raw
                                       END::text),
          FALSE)
      )
    ORDER BY jsonb_array_length(b.block) DESC, r.id DESC
    LIMIT 1
  ),
    -- UTM_RULES_END
    um.mapped_value,
    src.sale_initiated_category
  ) AS mapped_source_category
FROM (
-- Org filter policy (2026-09-21): the WHERE clauses below compare the raw row's own
-- organization_id directly. Wrapping the column in COALESCE over a LEFT JOIN made the
-- predicate non-sargable (no index, no per-org selectivity), which fed the rows=1
-- misestimate behind the 2026-09-18..20 nightly failures.
-- 2026-10-07: the Hevo-era account-to-org fallback join was removed. Every raw row now
-- carries its own organization_id, so no lookup table is consulted here any more.
WITH
-- Date-window the orders once, then scope the note-attribute aggregation to
-- those order_ids. raw_shopify_order_note_attributes has no date column, so it
-- is windowed indirectly via this join. Output is unchanged: paid_flags is only
-- ever consumed for orders that pass this same WHERE.
windowed_orders AS (
  SELECT ro.id
  FROM raw_shopify_orders ro
  WHERE ro.organization_id IS NOT NULL
    AND ro.organization_id = %(org)s
    AND (NOT %(exclude_pos)s OR upper(coalesce(ro.source_name, '')) <> 'POS')
    AND (ro.updated_at >= %(since)s OR (ro.created_at >= %(created_from)s AND ro.created_at < %(created_to)s))
),
attrs AS (
  SELECT
    na.order_id,
    COUNT(*) AS attr_rows,
    -- Shopify cart token. Third-party checkouts (GoKwik as `cart_token`,
    -- Shiprocket as `shopifyCartToken`) copy it onto the order even though they
    -- never populate raw_shopify_orders.cart_token, which makes this the only
    -- durable link back to the browsing session the web pixel recorded.
    -- Normalised to the bare token exactly as normalizeCartId() does in
    -- clevrr-web-pixel/src/index.js: strip the query string, then any gid:// path
    -- prefix. If the two sides diverge the order -> visitor join silently matches
    -- nothing, so keep them in lock-step.
    MAX(
      NULLIF(regexp_replace(split_part(btrim(na.value), '?', 1), '^.*/', ''), '')
    ) FILTER (
      WHERE LOWER(btrim(na.name)) IN ('shopifycarttoken', 'cart_token', 'carttoken')
        AND NULLIF(btrim(na.value),'') IS NOT NULL
    ) AS cart_token_attr,
    MAX(na.value) FILTER (
      WHERE LOWER(btrim(na.name)) = 'utm_source'
        AND NULLIF(btrim(na.value),'') IS NOT NULL
    ) AS utm_source_explicit,
    MAX((regexp_match(na.value,'(?i)(?:[?&]|^)utm_source=([^&#[:space:]]+)'))[1]) FILTER (
      WHERE NULLIF(btrim(na.value),'') IS NOT NULL
        AND (na.value ILIKE '%%?utm_%%' OR na.value ILIKE '%%&utm_%%' OR na.value ILIKE '%%utm_%%=%%')
    ) AS utm_source_in_url,

    MAX(na.value) FILTER (
      WHERE LOWER(btrim(na.name)) = 'utm_medium'
        AND NULLIF(btrim(na.value),'') IS NOT NULL
    ) AS utm_medium_explicit,
    MAX((regexp_match(na.value,'(?i)(?:[?&]|^)utm_medium=([^&#[:space:]]+)'))[1]) FILTER (
      WHERE NULLIF(btrim(na.value),'') IS NOT NULL
        AND (na.value ILIKE '%%?utm_%%' OR na.value ILIKE '%%&utm_%%' OR na.value ILIKE '%%utm_%%=%%')
    ) AS utm_medium_in_url,

    MAX(na.value) FILTER (
      WHERE LOWER(btrim(na.name)) = 'utm_campaign'
        AND NULLIF(btrim(na.value),'') IS NOT NULL
    ) AS utm_campaign_explicit,
    MAX((regexp_match(na.value,'(?i)(?:[?&]|^)utm_campaign=([^&#[:space:]]+)'))[1]) FILTER (
      WHERE NULLIF(btrim(na.value),'') IS NOT NULL
        AND (na.value ILIKE '%%?utm_%%' OR na.value ILIKE '%%&utm_%%' OR na.value ILIKE '%%utm_%%=%%')
    ) AS utm_campaign_in_url,

    MAX(na.value) FILTER (
      WHERE LOWER(btrim(na.name)) = 'utm_term'
        AND NULLIF(btrim(na.value),'') IS NOT NULL
    ) AS utm_term_explicit,
    MAX((regexp_match(na.value,'(?i)(?:[?&]|^)utm_term=([^&#[:space:]]+)'))[1]) FILTER (
      WHERE NULLIF(btrim(na.value),'') IS NOT NULL
        AND (na.value ILIKE '%%?utm_%%' OR na.value ILIKE '%%&utm_%%' OR na.value ILIKE '%%utm_%%=%%')
    ) AS utm_term_in_url,

    MAX(na.value) FILTER (
      WHERE LOWER(btrim(na.name)) = 'utm_content'
        AND NULLIF(btrim(na.value),'') IS NOT NULL
    ) AS utm_content_explicit,
    MAX((regexp_match(na.value,'(?i)(?:[?&]|^)utm_content=([^&#[:space:]]+)'))[1]) FILTER (
      WHERE NULLIF(btrim(na.value),'') IS NOT NULL
        AND (na.value ILIKE '%%?utm_%%' OR na.value ILIKE '%%&utm_%%' OR na.value ILIKE '%%utm_%%=%%')
    ) AS utm_content_in_url,

    MAX(na.value) FILTER (
      WHERE LOWER(btrim(na.name)) = 'utm_id'
        AND NULLIF(btrim(na.value),'') IS NOT NULL
    ) AS utm_id_explicit,
    MAX((regexp_match(na.value,'(?i)(?:[?&]|^)utm_id=([^&#[:space:]]+)'))[1]) FILTER (
      WHERE NULLIF(btrim(na.value),'') IS NOT NULL
        AND (na.value ILIKE '%%?utm_%%' OR na.value ILIKE '%%&utm_%%' OR na.value ILIKE '%%utm_%%=%%')
    ) AS utm_id_in_url,

    MAX(na.value) FILTER (
      WHERE LOWER(btrim(na.name)) = 'orig_referrer'
        AND NULLIF(btrim(na.value),'') IS NOT NULL
    ) AS orig_referrer_attr,

    COUNT(*) FILTER (
      WHERE (LOWER(na.name) ILIKE '%%fbclid%%' OR LOWER(na.value) ILIKE '%%fbclid%%')
    ) AS fbclid_hits,

    COUNT(*) FILTER (
      WHERE (LOWER(na.name) ILIKE '%%srsltid%%' OR LOWER(na.value) ILIKE '%%srsltid%%')
    ) AS srsltid_hits,

    -- Any Google *paid*-click marker. gclid is the ordinary web click id; gbraid/wbraid
    -- replace it on iOS app-to-web where Google withholds gclid; gad_source/gad_campaignid
    -- come from auto-tagging. Presence of any one of these is what separates a paid
    -- Shopping/PMax click from a free Google listing, which carries the SAME
    -- utm_source=google stamped by Shopify's Google & YouTube feed. srsltid is NOT here:
    -- it marks the free listing.
    COUNT(*) FILTER (
      WHERE (
          LOWER(na.name)  ILIKE '%%gclid%%'          OR LOWER(na.value) ILIKE '%%gclid%%'  OR
          LOWER(na.name)  ILIKE '%%gbraid%%'         OR LOWER(na.value) ILIKE '%%gbraid%%' OR
          LOWER(na.name)  ILIKE '%%wbraid%%'         OR LOWER(na.value) ILIKE '%%wbraid%%' OR
          LOWER(na.name)  ILIKE '%%gad_source%%'     OR LOWER(na.value) ILIKE '%%gad_source%%' OR
          LOWER(na.name)  ILIKE '%%gad_campaignid%%' OR LOWER(na.value) ILIKE '%%gad_campaignid%%'
        )
    ) AS gclid_hits,

    COUNT(*) FILTER (
      WHERE (
          LOWER(na.name)  ILIKE '%%checkout%%'        OR LOWER(na.value) ILIKE '%%checkout%%' OR
          LOWER(na.name)  ILIKE '%%checkout_token%%'  OR LOWER(na.value) ILIKE '%%checkout_token%%' OR
          LOWER(na.name)  ILIKE '%%cart_token%%'      OR LOWER(na.value) ILIKE '%%cart_token%%' OR
          LOWER(na.name)  ILIKE '%%transaction%%'     OR LOWER(na.value) ILIKE '%%transaction%%' OR
          LOWER(na.name)  ILIKE '%%order id%%'        OR LOWER(na.value) ILIKE '%%order id%%' OR
          LOWER(na.name)  ILIKE '%%order_id%%'        OR LOWER(na.value) ILIKE '%%order_id%%' OR
          LOWER(na.name)  ILIKE '%%breeze order id%%' OR LOWER(na.value) ILIKE '%%breeze order id%%'
        )
    ) AS checkout_hits
  FROM raw_shopify_order_note_attributes na
  JOIN windowed_orders w ON w.id = na.order_id
  GROUP BY na.order_id
),
-- Precompute the per-order attribution flags once (joined to the main query
-- below), replacing the previous per-row correlated-subquery explosion. The
-- branch logic in sale_initiated_category is unchanged. NOTE on byte-identical
-- output: attr_rows = COUNT(*) is always >= 1 for an existing group, and the
-- old code read these via a scalar subquery over `attrs` that returned NULL
-- when an order had no note attributes. To preserve that exactly, the CASE
-- below uses `pf.attr_rows = 0` (NOT COALESCE(...,0) = 0) so a missing pf row
-- stays NULL/false rather than matching the DRAFT short-circuit.
-- MATERIALIZED is load-bearing, not cosmetic. `paid_flags` is referenced exactly
-- once below, so PG12+ inlines it by default. raw_shopify_orders carries no index
-- on created_at/updated_at and autoanalyze rarely fires on it (10%% of 550k rows),
-- so the planner estimates rows=1 for the date window and picks a Nested Loop over
-- a Hash Join. Inlined, the whole attrs aggregation -- including a seq scan of the
-- 3.8 GB raw_shopify_orders -- then re-executes once per outer order (EXPLAIN shows
-- GroupAggregate loops=<order count>), making the transform quadratic in daily
-- order volume. That is what killed the nightly for thedeconstruct.in on 2026-09-18
-- through 09-20: 861s + 10,168s against a 7,200s activeDeadlineSeconds. Forcing one
-- evaluation took the same two passes to ~1.7s and ~6.6s measured on prod.
paid_flags AS MATERIALIZED (
  SELECT
    a.order_id,
    COALESCE(a.attr_rows, 0) AS attr_rows,
    a.cart_token_attr AS cart_token,
    LOWER(NULLIF(btrim(btrim(COALESCE(a.utm_source_explicit,  a.utm_source_in_url)) , '"'), '')) AS utm_source_norm,
    LOWER(NULLIF(btrim(btrim(COALESCE(a.utm_medium_explicit,  a.utm_medium_in_url)) , '"'), '')) AS utm_medium_norm,
    LOWER(NULLIF(btrim(btrim(COALESCE(a.utm_campaign_explicit,a.utm_campaign_in_url)), '"'), '')) AS utm_campaign_norm,
    LOWER(NULLIF(btrim(btrim(COALESCE(a.utm_term_explicit,    a.utm_term_in_url))   , '"'), '')) AS utm_term_norm,
    LOWER(NULLIF(btrim(btrim(COALESCE(a.utm_content_explicit, a.utm_content_in_url)), '"'), '')) AS utm_content_norm,
    LOWER(NULLIF(btrim(btrim(COALESCE(a.utm_id_explicit,     a.utm_id_in_url))     , '"'), '')) AS utm_id_norm,
    a.orig_referrer_attr AS orig_referrer,
    COALESCE(a.fbclid_hits, 0)   AS fbclid_hits,
    COALESCE(a.srsltid_hits, 0)  AS srsltid_hits,
    COALESCE(a.gclid_hits, 0)    AS gclid_hits,
    COALESCE(a.checkout_hits, 0) AS checkout_hits,
    -- Shopify's Google & YouTube channel product feed stamps a fixed
    -- utm_medium=product_sync / utm_campaign=sag_organic (also utm_content) on every feed
    -- click, paid or free. Combined with gclid_hits = 0 this identifies a free listing.
    (
      LOWER(NULLIF(btrim(btrim(COALESCE(a.utm_campaign_explicit,a.utm_campaign_in_url)), '"'), '')) ~* '^sag_organic$' OR
      LOWER(NULLIF(btrim(btrim(COALESCE(a.utm_content_explicit, a.utm_content_in_url)), '"'), '')) ~* '^sag_organic$' OR
      LOWER(NULLIF(btrim(btrim(COALESCE(a.utm_medium_explicit,  a.utm_medium_in_url)) , '"'), '')) ~* '^product_sync$'
    ) AS is_google_feed_utm,
    (
      (LOWER(NULLIF(btrim(btrim(COALESCE(a.utm_source_explicit,  a.utm_source_in_url)) , '"'), '')) ~* '(google([[:space:]]*ads)?|youtube|ga)') OR
      (LOWER(NULLIF(btrim(btrim(COALESCE(a.utm_medium_explicit,  a.utm_medium_in_url)) , '"'), '')) ~* '(google([[:space:]]*ads)?|youtube|ga)') OR
      (LOWER(NULLIF(btrim(btrim(COALESCE(a.utm_campaign_explicit,a.utm_campaign_in_url)), '"'), '')) ~* '(google([[:space:]]*ads)?|youtube|ga)') OR
      (LOWER(NULLIF(btrim(btrim(COALESCE(a.utm_term_explicit,    a.utm_term_in_url))   , '"'), '')) ~* '(google([[:space:]]*ads)?|youtube|ga)') OR
      (LOWER(NULLIF(btrim(btrim(COALESCE(a.utm_content_explicit, a.utm_content_in_url)), '"'), '')) ~* '(google([[:space:]]*ads)?|youtube|ga)')
    ) AS is_paid_google_any,
    (
      (LOWER(NULLIF(btrim(btrim(COALESCE(a.utm_source_explicit,  a.utm_source_in_url)) , '"'), '')) ~* '(meta|fb|ig|inst|instagram|facebook|reels|stories)') OR
      (LOWER(NULLIF(btrim(btrim(COALESCE(a.utm_medium_explicit,  a.utm_medium_in_url)) , '"'), '')) ~* '(meta|fb|ig|inst|instagram|facebook|reels|stories)') OR
      (LOWER(NULLIF(btrim(btrim(COALESCE(a.utm_campaign_explicit,a.utm_campaign_in_url)), '"'), '')) ~* '(meta|fb|ig|inst|instagram|facebook|reels|stories)') OR
      (LOWER(NULLIF(btrim(btrim(COALESCE(a.utm_term_explicit,    a.utm_term_in_url))   , '"'), '')) ~* '(meta|fb|ig|inst|instagram|facebook|reels|stories)') OR
      (LOWER(NULLIF(btrim(btrim(COALESCE(a.utm_content_explicit, a.utm_content_in_url)), '"'), '')) ~* '(meta|fb|ig|inst|instagram|facebook|reels|stories)')
    ) AS is_paid_meta_any,
    (
      LOWER(NULLIF(btrim(btrim(COALESCE(a.utm_source_explicit,  a.utm_source_in_url)) , '"'), '')) IS NOT NULL OR
      LOWER(NULLIF(btrim(btrim(COALESCE(a.utm_medium_explicit,  a.utm_medium_in_url)) , '"'), '')) IS NOT NULL OR
      LOWER(NULLIF(btrim(btrim(COALESCE(a.utm_campaign_explicit,a.utm_campaign_in_url)), '"'), '')) IS NOT NULL OR
      LOWER(NULLIF(btrim(btrim(COALESCE(a.utm_term_explicit,    a.utm_term_in_url))   , '"'), '')) IS NOT NULL OR
      LOWER(NULLIF(btrim(btrim(COALESCE(a.utm_content_explicit, a.utm_content_in_url)), '"'), '')) IS NOT NULL
    ) AS has_any_utm
  FROM attrs a
),
-- CHANNEL_RULES_BEGIN
-- Channel verdict for every windowed order. Spec: docs/attribution-channel-rules.md.
-- Twin of app/services/attribution/channel_rules.py: same signals, same token lists, same
-- rule order, same rule ids. The token regexes below are GENERATED from that module by
-- scripts/render_channel_rules_sql.py — edit the Python lists, re-render, never hand-edit.
-- P2: landing_site + referring_site when Shopify recorded a landing; otherwise the checkout
-- note attributes (pf.*). P0: lower, %%20/+ decoded, trimmed, quotes stripped; an unreplaced
-- ad-platform macro ("{{campaign.name}}", "%%7B...") is NULL.
sig AS (
  SELECT
    x.order_id,
    x.landing_observed,
    x.attr_rows,
    x.google_click,
    x.fbclid,
    x.srsltid,
    CASE WHEN x.src ~* '^\s*(\{|%%7b)' THEN NULL ELSE x.src END AS src,
    CASE WHEN x.med ~* '^\s*(\{|%%7b)' THEN NULL ELSE x.med END AS med,
    CASE WHEN x.cmp ~* '^\s*(\{|%%7b)' THEN NULL ELSE x.cmp END AS cmp,
    CASE WHEN x.trm ~* '^\s*(\{|%%7b)' THEN NULL ELSE x.trm END AS trm,
    CASE WHEN x.cnt ~* '^\s*(\{|%%7b)' THEN NULL ELSE x.cnt END AS cnt,
    CASE WHEN x.uid ~* '^\s*(\{|%%7b)' THEN NULL ELSE x.uid END AS uid,
    -- P1: the referrer host unless it is the store itself or a checkout / payment gateway
    -- bouncing the visitor back. NULL = no usable referrer.
    CASE
      WHEN x.ref_host IS NOT NULL
       AND x.ref_host IS DISTINCT FROM x.own_host
       AND x.ref_host !~ '(shopify|shop\.app|gokwik|shiprocket|razorpay|payu|phonepe|paytm|cashfree|juspay|paypal|stripe|breeze|shopflo|simpl|snapmint|zestmoney|magicpin|ccavenue|billdesk|instamojo)'
      THEN x.ref_host
    END AS ref_host
  FROM (
    SELECT
      ro.id AS order_id,
      -- Shopify sends '' as well as NULL for "no landing page"; both mean nothing observed.
      (NULLIF(BTRIM(ro.landing_site), '') IS NOT NULL) AS landing_observed,
      COALESCE(pf.attr_rows, 0) AS attr_rows,
      -- Google paid-click markers: gclid on the web, gbraid / wbraid on iOS app-to-web,
      -- gad_* from auto-tagging. srsltid is NOT here: it marks a free listing.
      (COALESCE(ro.landing_site ~* '[?&](gclid|gbraid|wbraid|gad_source|gad_campaignid)=', false)
        OR COALESCE(pf.gclid_hits, 0) > 0) AS google_click,
      (COALESCE(ro.landing_site ~* '[?&]fbclid=', false) OR COALESCE(pf.fbclid_hits, 0) > 0) AS fbclid,
      (COALESCE(ro.landing_site ~* '[?&]srsltid=', false) OR COALESCE(pf.srsltid_hits, 0) > 0) AS srsltid,
      NULLIF(LOWER(BTRIM(BTRIM(REPLACE(REPLACE(COALESCE((regexp_match(ro.landing_site, '(?i)(?:[?&]|^)utm_source=([^&#[:space:]]+)'))[1], pf.utm_source_norm), '%%20', ' '), '+', ' ')), '"')), '') AS src,
      NULLIF(LOWER(BTRIM(BTRIM(REPLACE(REPLACE(COALESCE((regexp_match(ro.landing_site, '(?i)(?:[?&]|^)utm_medium=([^&#[:space:]]+)'))[1], pf.utm_medium_norm), '%%20', ' '), '+', ' ')), '"')), '') AS med,
      NULLIF(LOWER(BTRIM(BTRIM(REPLACE(REPLACE(COALESCE((regexp_match(ro.landing_site, '(?i)(?:[?&]|^)utm_campaign=([^&#[:space:]]+)'))[1], pf.utm_campaign_norm), '%%20', ' '), '+', ' ')), '"')), '') AS cmp,
      NULLIF(LOWER(BTRIM(BTRIM(REPLACE(REPLACE(COALESCE((regexp_match(ro.landing_site, '(?i)(?:[?&]|^)utm_term=([^&#[:space:]]+)'))[1], pf.utm_term_norm), '%%20', ' '), '+', ' ')), '"')), '') AS trm,
      NULLIF(LOWER(BTRIM(BTRIM(REPLACE(REPLACE(COALESCE((regexp_match(ro.landing_site, '(?i)(?:[?&]|^)utm_content=([^&#[:space:]]+)'))[1], pf.utm_content_norm), '%%20', ' '), '+', ' ')), '"')), '') AS cnt,
      NULLIF(LOWER(BTRIM(BTRIM(REPLACE(REPLACE(COALESCE((regexp_match(ro.landing_site, '(?i)(?:[?&]|^)utm_id=([^&#[:space:]]+)'))[1], pf.utm_id_norm), '%%20', ' '), '+', ' ')), '"')), '') AS uid,
      LOWER((regexp_match(
        CASE WHEN NULLIF(BTRIM(ro.landing_site), '') IS NOT NULL THEN ro.referring_site ELSE pf.orig_referrer END,
        '^(?:[a-zA-Z][a-zA-Z0-9+.-]*://)?(?:www\.)?([^/:?#]+)'))[1]) AS ref_host,
      LOWER((regexp_match(ro.landing_site, '^(?:[a-zA-Z][a-zA-Z0-9+.-]*://)?(?:www\.)?([^/:?#]+)'))[1]) AS own_host
    FROM raw_shopify_orders ro
    JOIN windowed_orders w ON w.id = ro.id
    LEFT JOIN paid_flags pf ON pf.order_id = ro.id
  ) x
),
sigx AS (
  SELECT
    s.*,
    (s.src IS NOT NULL OR s.med IS NOT NULL OR s.cmp IS NOT NULL OR s.trm IS NOT NULL OR s.cnt IS NOT NULL OR s.uid IS NOT NULL) AS has_utm,
    COALESCE(s.med ~ '^(.*cp.*|ppc|retargeting|remarketing|paid.*|display|banner|social_paid|social paid|paidsocial)$', false) AS paid_medium,
    COALESCE(s.src ~ '(^|[^a-z0-9])(performancemax|googleads|adwords|youtube|google|pmax|gdn|gsn|ga)([^a-z0-9]|$)', false) AS google_src,
    COALESCE(s.src ~ '(^|[^a-z0-9])(instagram|messenger|facebook|stories|threads|reels|fbig|meta|fb|ig)([^a-z0-9]|$)', false) AS meta_src,
    COALESCE(s.src ~ '(^|[^a-z0-9])(sponsoredproducts|sponsoreddisplay|sponsoredbrands|amazonads|amazon|amzn)([^a-z0-9]|$)', false) AS amazon_src,
    (COALESCE(s.src ~ '^[0-9]{15,20}$', false) OR COALESCE(s.med ~ '^[0-9]{15,20}$', false) OR COALESCE(s.cmp ~ '^[0-9]{15,20}$', false) OR COALESCE(s.trm ~ '^[0-9]{15,20}$', false) OR COALESCE(s.cnt ~ '^[0-9]{15,20}$', false) OR COALESCE(s.uid ~ '^[0-9]{15,20}$', false)) AS meta_ad_id,
    (COALESCE(s.cmp ~ '(^|[^a-z0-9])(sag_organic)([^a-z0-9]|$)', false) OR COALESCE(s.cnt ~ '(^|[^a-z0-9])(sag_organic)([^a-z0-9]|$)', false)
      OR COALESCE(s.med ~ '(^|[^a-z0-9])(product_sync)([^a-z0-9]|$)', false)) AS feed,
    COALESCE(s.med ~ '(^|[^a-z0-9])(newsletter|emailer|e-mail|e_mail|email|mail|edm)([^a-z0-9]|$)', false) AS email_med,
    COALESCE(s.src ~ '(^|[^a-z0-9])(newsletter|sendinblue|clevertap|mailchimp|webengage|mailmodo|moengage|omnisend|sendgrid|klaviyo|netcore|brevo|email)([^a-z0-9]|$)', false) AS email_src,
    COALESCE(s.med ~ '(^|[^a-z0-9])(notification|whatsapp|push|text|rcs|sms|wa)([^a-z0-9]|$)', false) AS sms_med,
    COALESCE(s.src ~ '(^|[^a-z0-9])(whatsapp business|bitespeed|interakt|kwikchat|limechat|whatsapp|aisensy|gupshup|whats|wati|zoko|sms|wa)([^a-z0-9]|$)', false) AS sms_src,
    COALESCE(s.med ~ '(^|[^a-z0-9])(influencers|affiliates|ambassador|influencer|affiliate|creators|partners|creator|partner|collab|kol)([^a-z0-9]|$)', false) AS aff_med,
    COALESCE(s.src ~ '(^|[^a-z0-9])(influencers|affiliates|ambassador|influencer|partnerize|shareasale|affiliate|celebrity|creators|cuelinks|earnkaro|admitad|creator|collab|impact|cj)([^a-z0-9]|$)', false) AS aff_src,
    COALESCE(s.med ~ '(^|[^a-z0-9])(organic social|organic_social|social_media|socialmedia|linkinbio|social|story|post|bio)([^a-z0-9]|$)', false) AS social_med,
    COALESCE(s.src ~ '(^|[^a-z0-9])(instagram|messenger|pinterest|sharechat|facebook|linkedin|snapchat|telegram|discord|stories|threads|twitter|youtube|reddit|social|tiktok|quora|reels|fbig|josh|meta|moj|fb|ig|x)([^a-z0-9]|$)', false) AS social_src,
    COALESCE(s.src ~ '(^|[^a-z0-9])(duckduckgo|ecosia|google|yandex|baidu|brave|yahoo|bing)([^a-z0-9]|$)', false) AS search_src,
    COALESCE(s.med ~ '(^|[^a-z0-9])(product_sync|organic|search|seo)([^a-z0-9]|$)', false) AS organic_med,
    COALESCE(s.med ~ '(^|[^a-z0-9])(referral|referal|website|link|web)([^a-z0-9]|$)', false) AS referral_med,
    COALESCE(s.ref_host ~ '(^|\.)(google\.[a-z.]+|bing\.com|duckduckgo\.com|yahoo\.[a-z.]+|ecosia\.org|baidu\.com|yandex\.[a-z.]+|brave\.com)$', false) AS search_host,
    COALESCE(s.ref_host ~ '(^|\.)(facebook\.com|fb\.com|fb\.me|instagram\.com|threads\.net|tiktok\.com|pinterest\.[a-z.]+|twitter\.com|x\.com|t\.co|linkedin\.com|lnkd\.in|reddit\.com|snapchat\.com|youtube\.com|youtu\.be|telegram\.org|t\.me|sharechat\.com|quora\.com|discord\.com)$', false) AS social_host
  FROM sig s
),
-- MATERIALIZED for the same reason `paid_flags` is (see its comment above): a CTE
-- referenced exactly once is inlined by PG12+, and `sig` scans raw_shopify_orders, so
-- inlining lets the planner re-evaluate this whole chain once per outer order under a
-- Nested Loop. Forcing one evaluation here also pins `sig` and `sigx`, which feed it.
verdict AS MATERIALIZED (
  SELECT
    s.*,
    CASE
      WHEN s.google_click THEN 'R1'
      WHEN s.paid_medium AND s.google_src THEN 'R2'
      WHEN (s.paid_medium AND s.meta_src) OR (s.meta_ad_id AND (s.fbclid OR s.meta_src)) THEN 'R3'
      WHEN s.paid_medium AND s.amazon_src THEN 'R4'
      WHEN s.paid_medium THEN 'R5'
      WHEN s.email_med OR s.email_src THEN 'R6'
      WHEN s.sms_med OR s.sms_src THEN 'R7'
      WHEN s.aff_med OR s.aff_src THEN 'R8'
      WHEN s.social_med OR s.social_src OR s.meta_src THEN 'R9'
      WHEN s.search_src OR s.organic_med OR s.feed THEN 'R10'
      WHEN s.referral_med THEN 'R13'
      WHEN s.has_utm THEN 'R14'
      WHEN s.fbclid THEN 'R11'
      WHEN s.srsltid THEN 'R12'
      WHEN s.ref_host IS NOT NULL AND s.search_host THEN 'R18'
      WHEN s.ref_host IS NOT NULL AND s.social_host THEN 'R19'
      WHEN s.ref_host IS NOT NULL THEN 'R20'
      WHEN s.landing_observed THEN 'R21'
      WHEN s.attr_rows = 0 THEN 'R22'
      ELSE 'R23'
    END AS rule
  FROM sigx s
)
-- CHANNEL_RULES_END
SELECT
  ro.id AS order_id,
  -- Shopify's display name for the order ("#SC24134396"), store prefix/suffix included.
  -- Display-only; ro.id above stays the join key everywhere downstream.
  NULLIF(btrim(ro.name), '') AS order_name,
  'shopify' AS source_type,
  ro.organization_id AS organization_id,
  (ro.processed_at AT TIME ZONE 'Asia/Kolkata') AS order_date,
  (ro.customer::jsonb ->> 'id')::BIGINT AS customer_id,
  ro.subtotal_price::NUMERIC(14,2),
  ro.total_discounts::NUMERIC(14,2),
  (ro.total_shipping_price_set::jsonb -> 'shop_money' ->> 'amount')::NUMERIC(14,2) AS shipping_price,
  ro.total_tax::NUMERIC(14,2) AS tax_price,
  ro.total_price::NUMERIC(14,2),
  ro.currency,
  ro.financial_status,
  ro.fulfillment_status,
  CASE
    WHEN ro.fulfillments IS NOT NULL AND jsonb_array_length(ro.fulfillments::jsonb) > 0
    THEN ro.fulfillments::jsonb -> 0 ->> 'tracking_company'
    ELSE NULL
  END AS shipping_carrier,
  CASE
    WHEN ro.fulfillments IS NOT NULL AND jsonb_array_length(ro.fulfillments::jsonb) > 0
    THEN ro.fulfillments::jsonb -> 0 ->> 'shipment_status'
    ELSE NULL
  END AS shipment_status,
  (ro.cancelled_at AT TIME ZONE 'Asia/Kolkata') as cancelled_at,
  ro.cancel_reason as cancel_reason,
  (ro.created_at AT TIME ZONE 'Asia/Kolkata') as created_at,
  (ro.updated_at AT TIME ZONE 'Asia/Kolkata') as updated_at,
  (ro.shipping_address::jsonb ->> 'city') AS city,
  CASE v.rule
    WHEN 'R1' THEN 'PAID-GOOGLE'
    WHEN 'R2' THEN 'PAID-GOOGLE'
    WHEN 'R3' THEN 'PAID-META'
    WHEN 'R4' THEN 'PAID-AMAZON'
    WHEN 'R5' THEN 'PAID-OTHER'
    WHEN 'R6' THEN 'EMAIL'
    WHEN 'R7' THEN 'SMS'
    WHEN 'R8' THEN 'AFFILIATE'
    WHEN 'R9' THEN 'SOCIAL'
    WHEN 'R10' THEN 'ORGANIC'
    WHEN 'R11' THEN 'SOCIAL'
    WHEN 'R12' THEN 'ORGANIC'
    WHEN 'R13' THEN 'REFERRAL'
    WHEN 'R14' THEN 'OTHERS'
    WHEN 'R18' THEN 'ORGANIC'
    WHEN 'R19' THEN 'SOCIAL'
    WHEN 'R20' THEN 'REFERRAL'
    WHEN 'R21' THEN 'DIRECT'
    WHEN 'R22' THEN 'DRAFT'
    WHEN 'R23' THEN 'NO-SOURCE'
  END AS sale_initiated_category,
 (ro.discount_applications::jsonb -> 0 ->> 'code') AS discount_code,
  (ro.shipping_address::jsonb ->> 'province') AS state,
  (ro.shipping_address::jsonb ->> 'zip') AS zip,
  CASE
    WHEN ro.order_status_url IS NOT NULL THEN
      'https://admin.shopify.com/store/'
      || split_part(ro.order_status_url, '/', 4)
      || '/products/'
      || COALESCE((
           SELECT l.product_id::text
           FROM raw_shopify_order_lines l
           WHERE l.order_id = ro.id
           ORDER BY l.id
           LIMIT 1
         ), NULL)
    ELSE NULL
  END AS product_full_url,
  CONCAT(
    COALESCE(ro.customer::jsonb->>'first_name', ''),
    ' ',
    COALESCE(ro.customer::jsonb->>'last_name', '')
  ) AS customer_name,
  CASE
    WHEN ro.order_status_url IS NOT NULL THEN
      'https://admin.shopify.com/store/'
      || split_part(ro.order_status_url, '/', 4)
      || '/orders/' || ro.id::text
    ELSE NULL
  END AS order_status_url,
  ro.phone,
  ro.email,
  CASE
    WHEN ro.fulfillments IS NOT NULL AND jsonb_array_length(ro.fulfillments::jsonb) > 0
    THEN ro.fulfillments::jsonb -> 0 ->> 'tracking_number'
    ELSE NULL
  END AS tracking_number,
  pf.cart_token AS cart_token,
  -- Raw UTM values, landing_site first then checkout note attributes (pf.*_norm is
  -- already lower/trim/quote-stripped). %%20 and + decoded so a merchant can type the
  -- value as they see it in their own links. Lock-step with utm_mapping_service.
  --
  -- utm_source_raw is the token the merchant maps in Settings › UTM Mapping. An order
  -- whose link carried no utm_source still names its source through the same chain the
  -- pixel uses (services/utm_normalize.effective_source — keep the two in step):
  --   1. utm_source (landing_site, then note attributes)
  --   2. a platform click id: any Google marker -> 'gclid', fbclid -> 'fbclid'
  --   3. Google's Merchant Center listing id (free Shopping listing) -> 'srsltid'
  --   4. the referrer host ('instagram.com', 'google.com'), unless it is the store itself
  --      or a checkout / payment gateway bouncing the customer back
  --   5. 'direct' when we know the landing page but nothing referred it
  -- Without this, click-only, referral and direct orders could never be accepted and
  -- stayed hidden from Attribution Insights under strict mapping.
  -- The signals are computed once in the `sig` CTE (rule P0 / P1) and reused here, so
  -- the source token and the channel verdict see the same evidence by construction.
  COALESCE(
    v.src,
    CASE
      WHEN v.google_click THEN 'gclid'
      WHEN v.fbclid THEN 'fbclid'
      WHEN v.srsltid THEN 'srsltid'
      WHEN v.ref_host IS NOT NULL THEN v.ref_host
      WHEN v.landing_observed THEN 'direct'
    END
  ) AS utm_source_raw,
  NULLIF(LOWER(BTRIM(BTRIM(
    REPLACE(REPLACE(COALESCE(
      (regexp_match(ro.landing_site, '(?i)(?:[?&]|^)utm_medium=([^&#[:space:]]+)'))[1],
      pf.utm_medium_norm
    ), '%%20', ' '), '+', ' '),
  '"'))), '') AS utm_medium_raw,
  NULLIF(LOWER(BTRIM(BTRIM(
    REPLACE(REPLACE(COALESCE(
      (regexp_match(ro.landing_site, '(?i)(?:[?&]|^)utm_campaign=([^&#[:space:]]+)'))[1],
      pf.utm_campaign_norm
    ), '%%20', ' '), '+', ' '),
  '"'))), '') AS utm_campaign_raw,
  NULLIF(LOWER(BTRIM(BTRIM(
    REPLACE(REPLACE(COALESCE(
      (regexp_match(ro.landing_site, '(?i)(?:[?&]|^)utm_content=([^&#[:space:]]+)'))[1],
      pf.utm_content_norm
    ), '%%20', ' '), '+', ' '),
  '"'))), '') AS utm_content_raw
FROM raw_shopify_orders ro
LEFT JOIN paid_flags pf
  ON pf.order_id = ro.id
LEFT JOIN verdict v
  ON v.order_id = ro.id
WHERE ro.organization_id IS NOT NULL
  AND ro.organization_id = %(org)s
  AND (NOT %(exclude_pos)s OR upper(coalesce(ro.source_name, '')) <> 'POS')
  AND (ro.updated_at >= %(since)s OR (ro.created_at >= %(created_from)s AND ro.created_at < %(created_to)s))
) src
LEFT JOIN public.utm_mappings um
  ON um.organization_id = src.organization_id
 AND um.key_name        = 'utm_source'
 AND um.original_value  = src.utm_source_raw
ON CONFLICT (order_id, organization_id) DO UPDATE SET
  order_name = EXCLUDED.order_name,
  source_type = EXCLUDED.source_type,
  order_date = EXCLUDED.order_date,
  customer_id = EXCLUDED.customer_id,
  channel_id = EXCLUDED.channel_id,
  subtotal_price = EXCLUDED.subtotal_price,
  total_discounts = EXCLUDED.total_discounts,
  shipping_price = EXCLUDED.shipping_price,
  tax_price = EXCLUDED.tax_price,
  total_price = EXCLUDED.total_price,
  financial_status = EXCLUDED.financial_status,
  fulfillment_status = EXCLUDED.fulfillment_status,
  shipping_carrier = EXCLUDED.shipping_carrier,
  shipment_status = EXCLUDED.shipment_status,
  cancelled_at = EXCLUDED.cancelled_at,
  cancel_reason = EXCLUDED.cancel_reason,
  created_at = EXCLUDED.created_at,
  updated_at = EXCLUDED.updated_at,
  currency = EXCLUDED.currency,
  city = EXCLUDED.city,
  phone = EXCLUDED.phone,
  email = EXCLUDED.email,
  sale_initiated_category = EXCLUDED.sale_initiated_category,
  discount_code = EXCLUDED.discount_code,
  state = EXCLUDED.state,
  zip = EXCLUDED.zip,
  product_full_url = EXCLUDED.product_full_url,
  customer_name = EXCLUDED.customer_name,
  order_status_url = EXCLUDED.order_status_url,
  tracking_number = EXCLUDED.tracking_number,
  cart_token = EXCLUDED.cart_token,
  utm_source_raw = EXCLUDED.utm_source_raw,
  utm_medium_raw = EXCLUDED.utm_medium_raw,
  utm_campaign_raw = EXCLUDED.utm_campaign_raw,
  utm_content_raw = EXCLUDED.utm_content_raw,
  mapped_source_category = EXCLUDED.mapped_source_category,
  last_synced = CURRENT_TIMESTAMP
