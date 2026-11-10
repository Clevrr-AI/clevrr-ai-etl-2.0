# Prod cut-over to ETL v2 (hourly-capable sync)

Prepared 2026-11-10. Meta Ads moves to v2 now. Shopify and Google Ads stay on the legacy
nightly jobs until the blockers below are fixed.

## Where prod stands (read-only inventory, 2026-11-10)

| Item | Prod |
| --- | --- |
| ETL Alembic (`alembic_version_etl`) | never applied; `etl_schedules` does not exist |
| Orgs with an enabled connector | Shopify 8, Meta 6, Google 4 |
| Legacy watermarks | all at 10 Nov 00:00 IST; no v2 rows |
| ClickHouse tables v2 writes | all Replicated, except `order_mv_shard` (plain ReplacingMergeTree) |
| ClickHouse user `app` | no `REMOTE` grant (`clusterAllReplicas` fails) |
| `raw_google_ads_ad_group_ad` | has no `id` column (the sandbox copy does) |
| Image | `clevrr-ai/clevrr-registry/clevrr-etl-v2:cutover-20261110c` (pushed; `b` had the bound bug below) |


## 2026-11-10 run log

- 16:02 UTC, all steps applied at once. Migration 0001-0007 applied; 50 Meta watermark rows
  adopted; the async check matched (blissclub.in 9 Nov: 1,242 rows, Rs 5,13,832.93, sync =
  async); the poetrique.com pilot failed `check_meta`: advertising Postgres 21, ClickHouse 14.
- Cause: prod's `advertising.created_at` is timestamptz (sandbox: plain timestamp), so each day
  sits at 00:00 UTC in ClickHouse, but v2 used IST wall-clock bounds (05:30) for the delete and
  the check. The window's first day was neither deleted nor counted: poetrique.com 7 Nov is
  doubled in ClickHouse (14 rows, Rs 36,978.79). Fixed in image `c` (`ch_clock`: bounds follow
  the Postgres column type). Re-running the pilot on `c` deletes 7-10 Nov properly and repairs it.

## Why only Meta now

- **Shopify is blocked by `order_mv`.** `check_sales` compares `order_mv` row counts with
  Postgres exactly. Prod's `order_mv_shard` is not replicated, so a read lands on a random
  replica and the check fails most runs: the run is marked failed, the watermark does not
  move, and the org is re-run every hour. The fix is `clickhouse_migrations/0002` (and
  0003), which `etl ch-migrate` applies. As the `app` user it cannot copy the rows (no
  `REMOTE` grant) and would fail after dropping and renaming the live `order_mv` tables.
  Run it only as a ClickHouse admin, or after `GRANT REMOTE ON *.* TO app`. That is also
  the fix for the Sales page's Orders table (it shows about half the orders today).
- **Google Ads v2 has never run live.** It also needs `GOOGLE_ADS__CLIENT_ID`,
  `GOOGLE_ADS__CLIENT_SECRET` and `GOOGLE_ADS__DEVELOPER_TOKEN` on the job (legacy's names
  in `etl-config` are not read by v2). `cronjobs/etl-tick-google-ads.yaml` stays suspended.

## What changed in v2 for prod

- **Meta async fallback (legacy 653b6ff).** "Please reduce the amount of data" (HTTP 500,
  code 1) is no longer retried; the ad-level range runs as an async report job. Ranges are
  buffered, and a one-day range is never split. Without this, Blissclub-size days fail in v2
  on every tick. Unit-tested; no sandbox org has a working Meta token, so step 05 below
  checks it read-only against prod data.
- **0002 tolerates prod's schema drift.** Statements that name a column a live table lacks
  (`SET DEFAULT`, `OWNED BY`, primary keys) are skipped with `undefined_column`. Tested on a
  real Postgres with a prod-like `raw_google_ads_ad_group_ad`.
- **The v2 settings sit on the v2 jobs, not in `etl-config`.** `CLOUDSQL_INSTANCE` (legacy's
  image hard-codes it; v2 starts the proxy only when it is set), `ETL_V2_CUTOVER=true` and
  `CLICKHOUSE__CLUSTER=clevrr-cluster`. The legacy jobs read nothing new.
- **Tick deadline 2 h** (legacy's), so a long first run skips ticks instead of being killed.

## Steps (PowerShell; every command names the prod context)

Run them in order. Each Job runs once: to run one again, `kubectl delete job <name>` first.
A Job's result: `kubectl get job <name> -n etl --context=...` (COMPLETIONS 1/1) and its log.

**01. Postgres migration.** Additive. It builds a few indexes on the raw Shopify tables,
which blocks writes to them (not reads) for some minutes: run it outside 00:15-02:00 IST.

```
kubectl apply -f C:\Users\HP\clevrr\clevrr-ai-etl-2.0\cronjobs\cutover\01-pg-migrate.yaml --context=gke_clevrr-ai_asia-south1_clevrr-ckh-cluster-prod
kubectl logs job/etl-cutover-01-pg-migrate -n etl --context=gke_clevrr-ai_asia-south1_clevrr-ckh-cluster-prod
```

Good: `Running upgrade` lines up to `0007_etl_schedules`, then `etl exit code: 0`.

**03. Watermark adoption, dry run** (read-only, rolled back).

```
kubectl apply -f C:\Users\HP\clevrr\clevrr-ai-etl-2.0\cronjobs\cutover\03-adopt-dry-run.yaml --context=gke_clevrr-ai_asia-south1_clevrr-ckh-cluster-prod
kubectl logs job/etl-cutover-03-adopt-dry-run -n etl --context=gke_clevrr-ai_asia-south1_clevrr-ckh-cluster-prod
```

Good: `DRY RUN (['meta_ads']): 12 rows would be adopted` (6 orgs x meta-ads and
meta-ads-region) and no `NO v2 watermark` line.

**04. Watermark adoption.** Writes v2 rows into `public.etl_watermarks`; legacy's rows stay.

```
kubectl apply -f C:\Users\HP\clevrr\clevrr-ai-etl-2.0\cronjobs\cutover\04-adopt.yaml --context=gke_clevrr-ai_asia-south1_clevrr-ckh-cluster-prod
kubectl logs job/etl-cutover-04-adopt -n etl --context=gke_clevrr-ai_asia-south1_clevrr-ckh-cluster-prod
```

**05. Meta async check** (read-only: calls Meta, writes nothing). Yesterday's ad-level rows
for blissclub.in and poetrique.com, synchronous vs async report.

```
kubectl apply -f C:\Users\HP\clevrr\clevrr-ai-etl-2.0\cronjobs\cutover\05-check-meta-async.yaml --context=gke_clevrr-ai_asia-south1_clevrr-ckh-cluster-prod
kubectl logs job/etl-cutover-05-check-meta-async -n etl --context=gke_clevrr-ai_asia-south1_clevrr-ckh-cluster-prod
```

Good: one line per ad account with `"match": true`, or `"sync": "REFUSED: ..."` with numbers
under `"async"`. An `ERROR` under `"async"` stops the cut-over.

**07. Pilot: one org through v2 Meta** (poetrique.com, from its adopted watermark: 7 Nov to now).

```
kubectl apply -f C:\Users\HP\clevrr\clevrr-ai-etl-2.0\cronjobs\cutover\07-pilot-meta.yaml --context=gke_clevrr-ai_asia-south1_clevrr-ckh-cluster-prod
kubectl logs job/etl-cutover-07-pilot-meta -n etl --context=gke_clevrr-ai_asia-south1_clevrr-ckh-cluster-prod
```

Good: `etl.run_succeeded` for `meta-ads` and for `meta-ads-region`, `etl exit code: 0`.

**Cut-over.** Suspend the legacy Meta job, then start the hourly v2 tick. Do it before 18:55
UTC (00:25 IST) or after the legacy run has finished (about 19:10 UTC).

```
kubectl patch cronjob etl-all-meta-ads -n etl --context=gke_clevrr-ai_asia-south1_clevrr-ckh-cluster-prod --type=merge --patch-file C:\Users\HP\clevrr\clevrr-ai-etl-2.0\cronjobs\cutover\suspend.json
kubectl apply -f C:\Users\HP\clevrr\clevrr-ai-etl-2.0\cronjobs\etl-tick-meta-ads.yaml --context=gke_clevrr-ai_asia-south1_clevrr-ckh-cluster-prod
kubectl get cronjobs -n etl --context=gke_clevrr-ai_asia-south1_clevrr-ckh-cluster-prod
```

Good: `etl-all-meta-ads` SUSPEND True, `etl-tick-meta-ads` SUSPEND False. Every org runs
daily (no `etl_schedules` row); the adopted watermarks say "ran this slot", so the first
v2 runs are the 00:30 IST tick, legacy's time. Ticks before that start and skip every org.

**08. Intervals.** One command per org and source, from `clevrr-ai-etl-2.0` (it renders a
one-off prod Job from `cronjobs/etl-manual-prod.yaml`). Allowed: 1h, 2h, 3h, 4h, 6h, 8h, 12h,
daily. Slots start at 00:00 IST and each run lands at :30, e.g. 4h = 00:30, 04:30 ... 20:30 IST.
Only sources whose tick is on in prod take effect (Meta, until Shopify and Google move).

```
cd C:\Users\HP\clevrr\clevrr-ai-etl-2.0
uv run python scripts/manual_job.py "schedule set --org poetrique.com --source meta_ads --every 4h" --prod | kubectl apply -f - --context=gke_clevrr-ai_asia-south1_clevrr-ckh-cluster-prod
uv run python scripts/manual_job.py "schedule list" --prod | kubectl apply -f - --context=gke_clevrr-ai_asia-south1_clevrr-ckh-cluster-prod
kubectl logs -l job=etl-manual -n etl --context=gke_clevrr-ai_asia-south1_clevrr-ckh-cluster-prod --tail=20
```

Back to daily: `schedule unset --org <org> --source meta_ads`. Pause one org: add `--paused`.

## Rollback (Meta)

Legacy kept its own watermarks, so it re-pulls the days v2 handled.

```
kubectl patch cronjob etl-tick-meta-ads -n etl --context=gke_clevrr-ai_asia-south1_clevrr-ckh-cluster-prod --type=merge --patch-file C:\Users\HP\clevrr\clevrr-ai-etl-2.0\cronjobs\cutover\suspend.json
kubectl patch cronjob etl-all-meta-ads -n etl --context=gke_clevrr-ai_asia-south1_clevrr-ckh-cluster-prod --type=merge --patch-file C:\Users\HP\clevrr\clevrr-ai-etl-2.0\cronjobs\cutover\unsuspend.json
```

## Shopify

1. ClickHouse admin, inside one ClickHouse pod (the default user there is the admin). First the
   read-only check: both column lists must be `[]` (a column the new table has and prod lacks
   fails the copy after the old table is renamed; a column prod has and the new table lacks is
   dropped). Then print the current Distributed definitions and keep them (a rollback recreates
   them). Then the fix, then the read-only verify:

```
Get-Content -Raw C:\Users\HP\clevrr\clevrr-ai-etl-2.0\cronjobs\cutover\ch-order-tables-check.sql | kubectl exec -i chi-clevrr-ch-clevrr-cluster-0-0-0 -n clickhouse --context=gke_clevrr-ai_asia-south1_clevrr-ckh-cluster-prod -- clickhouse-client --multiquery
kubectl exec chi-clevrr-ch-clevrr-cluster-0-0-0 -n clickhouse --context=gke_clevrr-ai_asia-south1_clevrr-ckh-cluster-prod -- clickhouse-client --query "SHOW CREATE TABLE clevrr_db.order_mv FORMAT TSVRaw"
kubectl exec chi-clevrr-ch-clevrr-cluster-0-0-0 -n clickhouse --context=gke_clevrr-ai_asia-south1_clevrr-ckh-cluster-prod -- clickhouse-client --query "SHOW CREATE TABLE clevrr_db.order_click_attribution FORMAT TSVRaw"
Get-Content -Raw C:\Users\HP\clevrr\clevrr-ai-etl-2.0\cronjobs\cutover\ch-order-tables-fix.sql | kubectl exec -i chi-clevrr-ch-clevrr-cluster-0-0-0 -n clickhouse --context=gke_clevrr-ai_asia-south1_clevrr-ckh-cluster-prod -- clickhouse-client --multiquery
Get-Content -Raw C:\Users\HP\clevrr\clevrr-ai-etl-2.0\cronjobs\cutover\ch-order-tables-verify.sql | kubectl exec -i chi-clevrr-ch-clevrr-cluster-0-0-0 -n clickhouse --context=gke_clevrr-ai_asia-south1_clevrr-ckh-cluster-prod -- clickhouse-client --multiquery
```

   12 statements; `order_mv` is missing for the seconds between its DROP and CREATE, so run it
   outside 00:15-02:00 IST. The verify must show both `*_shard` tables `Replicated...` and each
   "new" count equal to its "old, distinct" count. The old rows stay in `*_shard_legacy`.
2. `09-adopt-shopify.yaml`, then `06-pilot-shopify.yaml` (poetrique.com): `etl.run_succeeded`.
3. Cut-over: suspend `etl-all-shopify` (patch with suspend.json) and apply
   `cronjobs/etl-tick-shopify.yaml` (`suspend: false` since 2026-11-10).

## Google Ads

v2 Google has never run live. The tick reads the OAuth client from `etl-config`'s legacy keys
(`configMapKeyRef`, nothing copied). Product performance (Shopping, PMax) is optional in v2:
if its ON CONFLICT finds no matching unique index on prod, that dataset is skipped and logged.

1. `10-adopt-google.yaml`, then `11-pilot-google.yaml` (thedeconstruct.in): `etl.run_succeeded`.
2. Cut-over: suspend `etl-all-google-ads` and apply `cronjobs/etl-tick-google-ads.yaml`
   (`suspend: false` since 2026-11-10).

Rollback for either: suspend its tick, unsuspend its `etl-all-*` job (suspend.json / unsuspend.json).

## Known gaps after the cut-over

- **Failures are silent.** v2 alerts go to Slack only when `ALERTS__SLACK_WEBHOOK_URL` is set,
  and it is not; legacy posted to Orchestra. Watch `etl_runs` / the job logs until it is set.
  (The sandbox ticks failed every hour from 3 to 10 Nov unnoticed: shapercult's tokens died.)
- **Dead tokens are not parked.** Legacy marks a connector for reconnect on an auth error;
  v2 fails the run and retries every tick.
- **Legacy's Orchestra "last sync" fields** are not updated by v2.
