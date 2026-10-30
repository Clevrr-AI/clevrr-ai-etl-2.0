# cronjobs_dev — the existing sandbox CronJobs, on the v2 image

These are legacy's own CronJob files (`clevrr-ai-server/ETL/cronjobs_dev/`), migrated
unchanged: same names, schedules, ConfigMap `etl-config`, Secret `etl-secrets`, service
account `cloud-sql-proxy` and node pool. The only edit is the image. The v2 image runs their
`ETL_*` settings through `etl legacy` (see `etl/legacy.py`):

| CronJob | Legacy settings | v2 run |
| --- | --- | --- |
| etl-all-shopify-catalog-daily, etl-all-shopify-products | shopify, `ETL_SCOPES=catalog` | `run shopify-catalog` |
| etl-all-shopify | shopify, `sales:psql_to_psql` | `run shopify-sales` |
| etl-all-meta-ads | meta_ads | `run meta-ads`, then `run meta-ads-region` |
| etl-all-google-ads | google_ads | `run google-ads` (needs `GOOGLE_ADS__*`, see below) |
| etl-products-cost (file etl-product-costs.yaml) | `ETL_CONNECTORS=internal`, costs | `run costs` |
| etl-custom-backfill | `ETL_CONNECTORS`, `ETL_ORG_IDS`, `ETL_START_DATE`, `ETL_END_DATE` | `run <pipeline> --org ... --from ... --to ...` |
| etl-all-delhivery, etl-all-amazon-ads | parked / dropped | stops with a message (keep them on the legacy image) |
| etl-tick-shopify, etl-tick-meta-ads, etl-tick-google-ads | `ETL_COMMAND=tick <source>` | `tick <source>`: the due orgs only (below) |

The Cloud SQL proxy runs inside the image (entrypoint.sh), as legacy's did, from
`CLOUDSQL_INSTANCE` in `etl-config`. Legacy's connection names (`POSTGRES_HOST`, `CKH_HOST`,
`CLICKHOUSE_CLUSTER`, ...) are translated to the v2 settings.

## Per-org sync frequency: the `etl-tick-*` CronJobs (2026-10-30)

`etl-tick-shopify`, `etl-tick-meta-ads` and `etl-tick-google-ads` run `etl tick <source>` every
hour (`0 * * * *` UTC = :30 IST) and run each org only when its `etl_schedules` interval slot
has started (hourly, 4h, 6h, daily ...; no row = daily). They replace `etl-all-shopify`,
`etl-all-meta-ads` and `etl-all-google-ads`: suspend those when a tick job is unsuspended.
Design and limits: `docs/scheduling.md`. Both files ship with `suspend: true`.

One-off commands (the migration, setting a schedule, a manual tick) run as a Job from the same
ConfigMap and Secret through `cronjobs_dev/etl-manual.yaml`:

```bash
C=--context=gke_clevrr-ai-sbx_asia-south1-a_clevrr-ckh-cluster-sandbox
IMG=asia-south1-docker.pkg.dev/clevrr-ai-sbx/clevrr-registry-sbx/clevrr-etl-v2:<tag the CronJobs run>
uv run python scripts/manual_job.py pg-migrate --image $IMG | kubectl apply -n etl $C -f -
uv run python scripts/manual_job.py "schedule set --org shapercult.com --source shopify --every 4h" --image $IMG   | kubectl apply -n etl $C -f -
uv run python scripts/manual_job.py "tick shopify" --image $IMG | kubectl apply -n etl $C -f -
kubectl logs -n etl $C -l job=etl-manual --tail=100
```

`etl legacy` passes a v2 `ETL_COMMAND` (`tick ...`, `schedule ...`, `pg-migrate`) through
unchanged, so these Jobs need nothing but the existing `etl-config`.

## What the cluster needs (one-time, sandbox only)

1. The v2 keys in `etl-config` (set 2026-10-19): `ETL_V2_CUTOVER=true` — the jobs write the
   sandbox's own tables, `public` / `clevrr_db` (the TL's decision; legacy's sandbox copy is
   retired) — `CLICKHOUSE__CLUSTER` (only the catalog jobs carry legacy's `CLICKHOUSE_CLUSTER`;
   without it a delete runs on the Distributed table and fails) and `RUN__ORG_ALLOWLIST` (pilot
   orgs only). Legacy ignores these keys:

   ```bash
   C=--context=gke_clevrr-ai-sbx_asia-south1-a_clevrr-ckh-cluster-sandbox
   kubectl patch configmap etl-config -n etl $C --type merge -p \
     '{"data":{"ETL_V2_CUTOVER":"true","CLICKHOUSE__CLUSTER":"sbx-cluster","RUN__ORG_ALLOWLIST":"[\"<org-a>\",\"<org-b>\"]"}}'
   ```

   Without `ETL_V2_CUTOVER` (or `POSTGRES__TARGET_SCHEMA` + `CLICKHOUSE__DATABASE` naming
   separate v2 targets) `etl legacy` refuses to run. Before the first run, bring the targets to
   v2's schema with `etl pg-migrate` and `etl ch-migrate` (done on the sandbox 2026-10-19; both
   keep legacy's tables and rows).
2. The image on each CronJob: `kubectl apply -f cronjobs_dev/<file>.yaml` (or CI, below).
3. For `etl-all-google-ads`: add `GOOGLE_ADS__CLIENT_ID`, `GOOGLE_ADS__CLIENT_SECRET` and
   `GOOGLE_ADS__DEVELOPER_TOKEN` to `etl-secrets` and reference them in its file (legacy's
   `GOOGLE_ADS_CLIENT_ID` etc. live unencrypted in `etl-config`; move them, do not copy).
4. Nodes: `wi-etl-pool-sbx` autoscales 0-2 (set 2026-10-19); a job waits 2-3 minutes for a node.

## Taking an org over from legacy (watermarks)

v2 keys a watermark by (source, pipeline name): `shopify-sales`, `meta-ads`, `meta-ads-region`,
`google-ads`. Legacy used `orders`, `ads`, `ads_region`. Without its rows v2 treats the org as
new (`first_window`: 3 days for sales, 365 days for Meta). Before the first run, copy legacy's
last position (done for the first pilot org on 2026-10-21):

```sql
INSERT INTO etl_watermarks (org_id, connector_key, entity, last_high_watermark, last_run_at, mode)
SELECT w.org_id, w.connector_key, v.entity, w.last_high_watermark, w.last_run_at, 'incremental'
FROM etl_watermarks w
JOIN (VALUES ('shopify', 'orders', 'shopify-sales'),
             ('meta_ads', 'ads', 'meta-ads'),
             ('meta_ads', 'ads_region', 'meta-ads-region'),
             ('google_ads', 'ads', 'google-ads')) AS v(ck, legacy_entity, entity)
  ON v.ck = w.connector_key AND v.legacy_entity = w.entity
WHERE w.org_id = '<org>'
ON CONFLICT (org_id, connector_key, entity) DO NOTHING;
```

Catalog and costs are full pulls and need no row. Narrow `RUN__ORG_ALLOWLIST` to the orgs
being taken over; `startingDeadlineSeconds: 600` on every job means unsuspending starts
nothing, the next scheduled run is the first.

Every file keeps `suspend: true`, as in the cluster today (the sandbox unsuspended four jobs
for one pilot org on 2026-10-21). Run one by hand:

```bash
kubectl create job --from=cronjob/etl-products-cost etl-products-cost-manual -n etl $C
kubectl logs -n etl $C -l job-name=etl-products-cost-manual -f
```

## Image updates

CI (`.github/workflows/ci.yaml`, branch `Staging`) pushes `clevrr-etl-v2:<git sha>` and runs
`kubectl set image cronjob/<name> -n etl etl=<image>:<sha>` on the jobs above, with the
Role in `_ci-rbac.yaml` (patch CronJobs in `etl`, nothing else). The tag in these files is the
first image only. If you `kubectl apply` a file after a CI deploy, it puts that old tag back:
apply only the file you changed, or set the live tag in it first.

`data-reliability-report` stays on the app image in legacy; the v2 report pipelines
(`reliability-*`) have no CronJob here yet.

## ckh-etl-ondemand (Cloud Run Job the app starts on connect)

Stays on the legacy image during parity (decision 2026-10-19). The v2 image accepts its
`ETL_*` overrides as they are. To switch it at cut-over: `gcloud run jobs update
ckh-etl-ondemand --image <v2 image> --update-env-vars CLICKHOUSE__HOST=<ClickHouse ILB
address>,CLICKHOUSE__CLUSTER=<cluster>,ETL_V2_CUTOVER=true` (its current ClickHouse host is
an in-cluster DNS name Cloud Run cannot resolve), and add it to the CI image updates.
