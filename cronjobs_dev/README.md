# cronjobs_dev — the existing sandbox CronJobs, on the v2 image

These are legacy's own CronJob files (`clevrr-ai-server/ETL/cronjobs_dev/`), migrated
unchanged: same names, schedules, ConfigMap `etl-config`, Secret `etl-secrets`, service
account `cloud-sql-proxy` and node pool. The only edit is the image. The v2 image runs their
`ETL_*` settings through `etl legacy` (see `etl/legacy.py`):

| CronJob | Legacy settings | v2 run |
|---|---|---|
| etl-all-shopify-catalog-daily, etl-all-shopify-products | shopify, `ETL_SCOPES=catalog` | `run shopify-catalog` |
| etl-all-shopify | shopify, `sales:psql_to_psql` | `run shopify-sales` |
| etl-all-meta-ads | meta_ads | `run meta-ads`, then `run meta-ads-region` |
| etl-all-google-ads | google_ads | `run google-ads` (needs `GOOGLE_ADS__*`, see below) |
| etl-products-cost (file etl-product-costs.yaml) | `ETL_CONNECTORS=internal`, costs | `run costs` |
| etl-custom-backfill | `ETL_CONNECTORS`, `ETL_ORG_IDS`, `ETL_START_DATE`, `ETL_END_DATE` | `run <pipeline> --org ... --from ... --to ...` |
| etl-all-delhivery, etl-all-amazon-ads | parked / dropped | stops with a message (keep them on the legacy image) |

The Cloud SQL proxy runs inside the image (entrypoint.sh), as legacy's did, from
`CLOUDSQL_INSTANCE` in `etl-config`. Legacy's connection names (`POSTGRES_HOST`, `CKH_HOST`,
`CLICKHOUSE_CLUSTER`, ...) are translated to the v2 settings.

## What the cluster needs (one-time, sandbox only)

1. The v2 targets in `etl-config`, so the jobs write `etl_v2` / `clevrr_db_v2` and only the
   pilot orgs, plus the ClickHouse cluster name (only the catalog jobs carry legacy's
   `CLICKHOUSE_CLUSTER`; without it a delete runs on the Distributed table and fails). Without them `etl legacy` refuses to run (it will not write legacy's tables):

   ```bash
   C=--context=gke_clevrr-ai-sbx_asia-south1-a_clevrr-ckh-cluster-sandbox
   kubectl patch configmap etl-config -n etl $C --type merge -p \
     '{"data":{"POSTGRES__TARGET_SCHEMA":"etl_v2","CLICKHOUSE__DATABASE":"clevrr_db_v2","CLICKHOUSE__CLUSTER":"sbx-cluster","RUN__ORG_ALLOWLIST":"[\"<org-a>\",\"<org-b>\"]"}}'
   ```

   Legacy ignores these keys. At cut-over they are removed and `ETL_V2_CUTOVER=true` is set.
2. The image on each CronJob: `kubectl apply -f cronjobs_dev/<file>.yaml` (or CI, below).
3. For `etl-all-google-ads`: add `GOOGLE_ADS__CLIENT_ID`, `GOOGLE_ADS__CLIENT_SECRET` and
   `GOOGLE_ADS__DEVELOPER_TOKEN` to `etl-secrets` and reference them in its file (legacy's
   `GOOGLE_ADS_CLIENT_ID` etc. live unencrypted in `etl-config`; move them, do not copy).
4. Nodes: `wi-etl-pool-sbx` autoscales 0-2 (set 2026-10-19); a job waits 2-3 minutes for a node.

Every file keeps `suspend: true`, as in the cluster today. Run one by hand:

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
