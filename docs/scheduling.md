# Per-org sync frequency (`etl tick`, `etl schedule`)

Each org can have its own sync interval per source (Shopify, Meta Ads, Google Ads): every
hour, every 4 hours, every 6 hours, once a day. Built 2026-10-30 for the TL's ask; the
founder confirmed Shopify data can be pulled hourly, and Meta and Google serve the current
day's figures as often as asked (the research is in the memory note `etl-configurable-frequency`).

## How it works

- **`etl_schedules`** (Alembic `0007`, ETL-owned): one row per `(org_id, connector_key)` with
  `interval_minutes` in `60, 120, 180, 240, 360, 480, 720, 1440` (every value divides a day),
  `enabled` and a free-text `note`. **No row means daily**, as the nightly jobs behaved.
  `enabled = false` pauses the org for that source.
- **`etl tick <source>`** runs every hour from the `etl-tick-*` CronJobs (`0 * * * *` UTC, that
  is :30 IST). It loads the source's orgs (same allowlist and sharding as `etl run`), their
  schedules and each pipeline's last successful run (`etl_watermarks.last_run_at`), and runs
  the pipeline for the orgs whose **slot** has started since that run. Slots are cut from IST
  midnight: a 4-hour org has slots at 00:00, 04:00, 08:00 ... IST and runs at 00:30, 04:30,
  08:30 ... IST; a daily org runs once, at the 00:30 IST tick. Nothing drifts, and an org whose
  run failed is retried at the next tick of the same slot.
- Pipelines per source (`etl/scheduler.py`): `shopify -> shopify-sales`,
  `meta_ads -> meta-ads, meta-ads-region`, `google_ads -> google-ads`. Full pulls
  (`shopify-catalog`, `costs`) and the reliability reports keep their own daily CronJobs.
- **Windows.** Shopify sales already ran on `updated_at_min` with a 6-hour look-back, so it
  works at any interval. Meta and Google used to run on IST dates `[watermark, today)` and
  never fetched today; now an incremental run includes the day the window ends in (today,
  partial) and re-pulls a **3-day look-back** (Meta keeps attributing conversions for days,
  Google up to 15 hours). A backfill's `--to` stays exclusive. The raw tables are keyed per
  day and the transforms delete-and-insert the window's days, so re-pulling a day is idempotent.

## Operating it

```bash
etl schedule list [--source shopify]
etl schedule set --org shapercult.com --source shopify --every 4h --note "pilot"
etl schedule set --org shapercult.com --source meta_ads --every 6h
etl schedule set --org a.com --source google_ads --every daily --paused   # keep the row, run nothing
etl schedule unset --org a.com --source shopify                          # back to daily
etl tick shopify                                                         # what the CronJob runs
```

On the sandbox these run as one-off Jobs from the same ConfigMap (`scripts/manual_job.py`,
see `cronjobs_dev/README.md`). The `etl-tick-*` CronJobs replace `etl-all-shopify`,
`etl-all-meta-ads` and `etl-all-google-ads`: suspend those when the tick jobs are on, or a
daily org runs twice a night (harmless, idempotent, but wasted API calls).

## Limits to know

- **Google Ads quota.** Basic API access allows 15,000 operations per sliding day; one org run
  is about 13 `searchStream` calls per customer. Hourly fits about 48 customers, 4-hourly about
  190; Standard access has no cap. Check the developer token's tier before setting many orgs
  below 4 hours.
- **Meta rate limits** are far away (190,000 + 400 per active ad Insights calls per ad account
  per hour on the Standard tier), but every tick re-pulls creatives and structure for the ads
  seen; skipping those on intraday ticks is a possible optimisation.
- **Intraday numbers move.** Spend, clicks and impressions are near real time; Meta conversions
  restate for up to 7 days, Google conversions take 3 to 15 hours. "Today" is a snapshot.
- **ClickHouse mutations.** `ChWriter.replace` deletes with `ALTER TABLE ... DELETE`; an hourly
  org issues one small mutation per table per tick. Fine for a pilot; watch
  `system.mutations` as org counts grow.
- A tick's Job has `activeDeadlineSeconds: 3500` and `concurrencyPolicy: Forbid`, so a long
  run skips at most one hourly tick. A run killed by the deadline does not advance the
  watermark and is retried at the next tick.
