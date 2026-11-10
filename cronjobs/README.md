# cronjobs — the production CronJobs (NOT APPLIED, except as `docs/prod-cutover.md` says)

Legacy's `clevrr-ai-server/ETL/cronjobs/` files, migrated unchanged except the image, which
is `clevrr-etl-v2:CHANGE-ME` until cut-over. `_namespace-guardrails.yaml` is the same file.

Do not apply during parity: with legacy's `etl-config` alone, `etl legacy` refuses to run
(its targets would be `public` / `clevrr_db`), and with `ETL_V2_CUTOVER=true` it writes
production. Cut-over (plan Phase 7): sign off parity on sandbox, fill the image tag, set
`ETL_V2_CUTOVER=true` in prod `etl-config`, apply these files, and suspend nothing else:
they replace legacy's jobs under the same names.

Same `etl legacy` mapping as `cronjobs_dev/README.md`.

## Cut-over, 2026-11-10 (`docs/prod-cutover.md`)

`etl-tick-meta-ads.yaml` / `etl-tick-shopify.yaml` / `etl-tick-google-ads.yaml` carry the
image `clevrr-etl-v2:cutover-20261110b` and the v2 settings legacy's `etl-config` lacks
(`CLOUDSQL_INSTANCE`, `ETL_V2_CUTOVER`, `CLICKHOUSE__CLUSTER`) on the job itself, so the
shared ConfigMap is not touched. Meta goes first; the Shopify tick stays suspended until
`order_mv` is replicated, the Google tick until v2 Google has the `GOOGLE_ADS__*` secrets and
a pilot. `cutover/` holds the one-off Jobs (inventory, migration, watermark adoption, a
read-only Meta async check, pilots) and the suspend / unsuspend patches.
