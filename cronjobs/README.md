# cronjobs — the production CronJobs (NOT APPLIED)

Legacy's `clevrr-ai-server/ETL/cronjobs/` files, migrated unchanged except the image, which
is `clevrr-etl-v2:CHANGE-ME` until cut-over. `_namespace-guardrails.yaml` is the same file.

Do not apply during parity: with legacy's `etl-config` alone, `etl legacy` refuses to run
(its targets would be `public` / `clevrr_db`), and with `ETL_V2_CUTOVER=true` it writes
production. Cut-over (plan Phase 7): sign off parity on sandbox, fill the image tag, set
`ETL_V2_CUTOVER=true` in prod `etl-config`, apply these files, and suspend nothing else:
they replace legacy's jobs under the same names.

Same `etl legacy` mapping as `cronjobs_dev/README.md`.
