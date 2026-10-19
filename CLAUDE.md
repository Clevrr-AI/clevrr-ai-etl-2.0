# clevrr-ai-etl-2.0

This repo is the Clevrr v2 ETL: it fetches provider data (Shopify, Meta, Google Ads, Amazon Ads, Delhivery, pixel) and loads it into Postgres and ClickHouse. It replaces `clevrr-ai-server/ETL` (Kubernetes CronJobs). The legacy repo is frozen: read it for reference, do not edit it. The API is `clevrr-backend-2.0`; the two repos share the databases, with explicit table ownership (below).

## Sources of truth

- `migration.md`, Phase 7, in `clevrr-backend-2.0`, is the plan: boundary contract with the API, step model, port order, checks, scheduling, schema, CI. Each engineer keeps a local copy. Never commit it.
- If the code, the plan and a request disagree, stop. Name the conflict and ask.

## Stack

Python 3.12 with `uv` and `uv.lock`. Synchronous clients (a batch process): psycopg 3 with a pool, `clickhouse-connect`, httpx. pydantic-settings, structlog. Alembic for the ETL's Postgres tables. Cloud Run Jobs + Cloud Scheduler + Cloud Workflows (no orchestrator product). Dev tools: ruff, pyright strict, pytest with testcontainers, import-linter.

## Layout

- `etl/core/`: settings, logging, db clients, the step model (`model.py`), the run ledger, alerts, the internal API client, the connector source.
- `etl/sources/<source>/`: provider clients and their extract code.
- `etl/steps/`: idempotent units: extract → stage raw → load Postgres → sync ClickHouse → check.
- `etl/checks/`: row counts and totals against the source, duplicates, null guards. A failed check fails the run.
- `etl/runner.py`: runs one pipeline for a set of orgs. `etl/pipelines.py`: the declarative list. `etl/cli.py`: the `etl` command.
- `alembic/` (version table `alembic_version_etl`), `clickhouse_migrations/`, `infra/`, `tests/`.

## Table ownership

- This repo owns the data tables (raw_*, orders, order_lines, refunds, products, ad metrics, shipments, costs) and the run ledger (`etl_runs`, `etl_steps`, `etl_watermarks`) through its Alembic, and all ClickHouse DDL through `clickhouse_migrations/`.
- `clevrr-backend-2.0` owns the application tables (users, organizations, `organization_connectors`, dashboards, credits, chat). This repo reads `organization_connectors` until the internal API exists, and never migrates it.

## Rules

1. A step is `def step(ctx, org, window) -> StepResult`. It is idempotent: a rerun of the same window leaves the same rows. It reads no environment variables and keeps no module state.
2. Read environment variables only in `etl/core/settings.py` and `etl/legacy.py` (the legacy-settings boundary for the existing CronJobs). Give no secret a default.
3. Do not build SQL with f-strings, `.format()`, `%` or `+`. Pass values as parameters (`%(name)s` for Postgres, `{name:Type}` for ClickHouse). Take identifiers from `psycopg.sql` or fixed constants. Put a query builder in a file named `sql_*.py`.
4. Write only the target schema and database. Use unqualified table names. Name `public.<table>` only to read a table the backend owns (`organizations`, `organization_connectors`, `connectors`, `connector_endpoints`, `utm_rules`, `utm_mappings`, `attribution_channel_rules`). Never name `clevrr_db.`. `scripts/check_rules.py` enforces this.
5. A failure is a failure. A page cap reached, a missing token, a failed check: raise `StepFailed`. Never log and go on. One org's failure never stops the other orgs.
6. Only the runner moves a watermark, after the pipeline's last step succeeds, on an incremental run.
7. Never log a credential or a provider URL that carries a token. Log the exception type and a short message.
8. Do not use `print` or `sys.path` edits. Catch every exception only at the runner and heartbeat boundaries, with a `noqa` that says why.
9. Keep every file under 400 lines and every function under 60 lines.
10. Put Postgres DDL only in Alembic migrations and ClickHouse DDL only in `clickhouse_migrations/`. A step never runs `ALTER` or `CREATE` (legacy loaders deadlocked on them).
11. Call a provider API live only for the orgs in `RUN__ORG_ALLOWLIST` until parity with legacy is proven.
12. Record every task in the "Change log" at the end of this file, in ASD-STE100 style.

## Commands

- Install: `uv sync`, then `uv run pre-commit install`. Copy `.env.example` to `.env` and fill it (sandbox only).
- Migrate: `uv run etl pg-migrate` (Alembic, the target schema) and `uv run etl ch-migrate` (ClickHouse, the target database)
- Run: `uv run etl list`, `uv run etl run <pipeline> --org <id> [--from YYYY-MM-DD --to YYYY-MM-DD]`, `uv run etl sweep`
- Test: `uv run pytest`. Integration tests need Docker Desktop and skip without it. Tests are local only: git ignores `tests/`, and CI does not run them.
- Lint: `uv run ruff check .` and `uv run ruff format --check .`
- Types: `uv run pyright`
- Rules: `uv run lint-imports` and `uv run python scripts/check_rules.py`
- Image: `docker build -t clevrr-etl-v2 .`
- CronJobs: `cronjobs_dev/README.md` (the existing sandbox jobs, v2 image). `cronjobs/` is prod; never apply it during parity.
- Infra: `infra/` (terraform). Read `infra/README.md`. Never apply without the engineer's go; never the prod project.
- Demo data (manual): `uv run python -m tools.demo.run --mode range --start YYYY-MM-DD --end YYYY-MM-DD`

## Working agreements

- Write docs and change log entries in ASD-STE100: short sentences, active voice, one instruction per sentence.
- Do not commit or push unless the engineer asks.
- Sandbox only (`clevrr-ai-sbx`). Never run a command against the prod project `clevrr-ai`, not even a read.

## Decisions (2026-10-17)

- Credentials: the ETL reads `public.organization_connectors` behind `etl/core/connectors.py` (`ConnectorSource`). It moves to the backend's internal API (`/internal/connectors*`, ADR-0003) after the agent phase, without a change to the pipelines.
- Parity with legacy: the ETL writes the sandbox Postgres schema `etl_v2` and the ClickHouse database `clevrr_db_v2`, and the checks compare them with legacy's own sandbox tables. Legacy's sandbox CronJobs keep running; v2 never writes their tables.
- Live provider calls: the pilot orgs only (`legendoftoys.com`, `shapercult.com`, `mydesignation.com`), read-only, with their sandbox credentials.
- Settings use nested names with a double underscore (`POSTGRES__HOST`), as in clevrr-backend-2.0: one name per setting, no aliases.
- Amazon Ads is not ported (decision 2026-10-17): the product switched it off. Legacy's `etl-all-amazon-ads` CronJob keeps its own schedule until legacy is retired.
- Delhivery is deferred (decision 2026-10-17): port it after the other sources. Legacy keeps its own CronJob until then.
- Google Ads: verified by tests only; no live run yet (decision 2026-10-19). Its OAuth client is `GOOGLE_ADS__CLIENT_ID` / `GOOGLE_ADS__CLIENT_SECRET`; legacy keeps them in the sandbox ConfigMap `etl-config`.
- Pixel jobs (attribution, probabilistic stitch, conversion export) are not ETL pipelines: they move to clevrr-backend-2.0 as v2 workers, next to pixel ingest (decision 2026-10-19).
- Runtime (decision 2026-10-19): the EXISTING `etl-all-*` CronJobs in the existing GKE cluster (namespace `etl`), unchanged except the image: their files are migrated into `cronjobs_dev/` (sandbox) and `cronjobs/` (prod, never applied during parity). The v2 image runs their `ETL_*` settings through `etl legacy`, with the Cloud SQL proxy inside the image as legacy had it. `infra/` stays as the record of the GCP side; its Cloud Run Jobs and Workflow are kept but unscheduled.
- `ckh-etl-ondemand` (the Cloud Run Job the app starts on connect) stays on the legacy image during parity (decision 2026-10-19); it switches at cut-over. The v2 image already accepts its `ETL_*` overrides.
- Live verification runs use shapercult.com only, with a window of a few days (decision 2026-10-19), so each source finishes quickly. Run other pilot orgs only when asked.
- During parity v2 never queues `creative_analysis_jobs` (the legacy worker turns them into Gemini spend) and never calls `/reports/auto_ingestion`; both move to the internal API later.

## Kept for parity, fix after

Legacy behaviour that v2 copies on purpose, so the parity checks compare like with like. Fix each one after parity, in one change, with the difference written down.

- `products.created_at` / `updated_at` go through `AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata'`, which stores them 5.5 hours early (`etl/sources/shopify/sql/catalog_products.sql`).
- ClickHouse `products` has `TTL created_at + 5 years`: products created more than 5 years ago disappear from ClickHouse, while Postgres keeps them. `check_catalog` compares only the newer products for this reason.
- Shopify sales: the sanitized timestamps are IST wall-clock values in timestamp-without-time-zone columns (the session time zone is Asia/Kolkata, `postgres.session_timezone`), and ClickHouse stores them labelled UTC.
- Meta (copied from legacy): no look-back, so conversions Meta attributes later never reach `advertising`. Creative and structure failures are logged and recorded in the step detail, not failed. `campaign_structure`'s `acct` CTE reads every org's `advertising`. The region pipeline's first run does not align its start to the org's ad history (legacy did).
- Google Ads (copied from legacy): no look-back. Product performance, the click map and the ad creative snapshot are best-effort (logged, recorded in the step detail). `raw_google_ads_conversion_action` has no org column (keyed on `resource_name`). The ad performance mapping reads `adGroup.name`/`status`, which its query does not select (always NULL).
- ClickHouse `shipments` (and `shipment_scans`) are ReplacingMergeTree behind a `rand()` Distributed key, like `order_click_attribution` before `0003`: a re-synced row can land on the other shard. Shard them by the key when Delhivery is ported.
- Shopify variant `cost` is read from the REST variant, but REST keeps cost on the inventory item, so the field is usually empty and `products.cost_per_item` stays NULL unless the app sets it.

## Change log

Newest first. One entry per task.

### 2026-10-19 · Run the ETL in the existing CronJobs, like legacy (Phase 7.5, revised)
- Phase: 7.5, 7.7 · Area: cronjobs, image, cli, infra, CI · Type: change · Risk: medium (not applied yet)
- Changed:
  - `cronjobs_dev/` and `cronjobs/`: legacy's own CronJob files, migrated unchanged except the image (`clevrr-etl-v2`). The demo-data CronJob is not migrated (v2 demo data is `tools/demo`).
  - `etl legacy` (`etl/legacy.py`): maps a job's `ETL_COMMAND`, `ETL_SCOPES`, `ETL_GROUP_PHASES`, `ETL_CONNECTORS`, `ETL_ORG_IDS` and dates to `etl run` invocations; translates legacy's connection names; a parity guard refuses `public` / `clevrr_db` as targets until `ETL_V2_CUTOVER=true`. Delhivery and Amazon jobs stop with a message.
  - The image: the Cloud SQL proxy binary and `entrypoint.sh`, which starts the proxy from `CLOUDSQL_INSTANCE` and runs `etl legacy` when legacy settings are present; plain `etl` arguments still work.
  - `cronjobs_dev/_ci-rbac.yaml`: CI may patch the CronJobs' image, nothing else. `infra/k8s.tf`: CI reads the cluster's credentials. CI moves the seven ETL CronJobs to each new image.
- Why: the engineer's decision (2026-10-19): use the existing jobs and their configuration as they are; only the image changes.
- Files: cronjobs/*, cronjobs_dev/*, etl/legacy.py, etl/cli.py, entrypoint.sh, Dockerfile, infra/k8s.tf, .github/workflows/ci.yaml, pyproject.toml, CLAUDE.md; local only: tests/unit/test_legacy.py
- Verify: every mapping in the sandbox files has a unit test; `docker build` works; in the image, legacy settings without targets are refused by the guard, and with targets the job plans its runs and fails only at the database (none in the test). ruff, pyright strict, the rules and `terraform validate` are clean.
- Applied in sandbox on 2026-10-19, with the engineer's go:
  - `etl-config` got `POSTGRES__TARGET_SCHEMA=etl_v2`, `CLICKHOUSE__DATABASE=clevrr_db_v2`, `CLICKHOUSE__CLUSTER=sbx-cluster` and `RUN__ORG_ALLOWLIST` (the pilot orgs). Legacy ignores them. The cluster key is needed because only the catalog CronJobs carry legacy's `CLICKHOUSE_CLUSTER`; the first test run failed on a Distributed-table delete without it.
  - `kubectl set image` moved the 7 ETL CronJobs (`etl-all-shopify`, `-catalog-daily`, `-products`, `-meta-ads`, `-google-ads`, `etl-products-cost`, `etl-custom-backfill`) to `clevrr-etl-v2:manual-20261019-k8s`. Everything else on them is as it was; all stay suspended. Delhivery and Amazon stay on the legacy image.
  - `_ci-rbac.yaml` applied; terraform added the CI cluster-viewer binding (and reset `client` metadata on the 12 Cloud Run Jobs that `gcloud` had touched; no other change).
  - Node pool `wi-etl-pool-sbx`: autoscaling on, min 0, max 2.
  - The live CronJob is named `etl-products-cost` (its file is `etl-product-costs.yaml`); CI and the README use the live name.
  - Test: `kubectl create job --from=cronjob/etl-products-cost` on the switched job succeeded (20 s on a freshly scaled node): the proxy started with the pod's identity, the legacy settings were translated, `run costs` wrote the org's cost rows, exit 0. The first attempt failed before the `CLICKHOUSE__CLUSTER` key was added.
- Correction: the literal I had called a committed database password in legacy's CronJob files is `ORCHESTRA_CHANNEL_UID`, a notification channel id.
- `etl legacy` also accepts the second spelling `CLICKHOUSE_HOST/PORT/USER/PASSWORD/DB` (the on-demand job and the reliability report use it; legacy's code read only `CKH_*`, so the prod on-demand job's `CLICKHOUSE_HOST` was ignored). `CKH_*` wins when both are set.
- On-demand: the sandbox `ckh-etl-ondemand` job's ClickHouse host is an in-cluster DNS name that Cloud Run cannot resolve, so legacy's on-demand runs fail at the ClickHouse stage there. At cut-over set `CLICKHOUSE__HOST` to the ILB address on that job.

### 2026-10-19 · Turn on CI deploys from Staging (Phase 7.7)
- Phase: 7.7 · Area: CI, infra · Type: change · Risk: low (sandbox, narrow permissions)
- Changed:
  - CI runs on pushes to `main` and `Staging`. The `deploy` job runs on `Staging` only, and only when the repository variable `GCP_WIF_PROVIDER` is set.
  - `infra/`: new variable `deploy_branch` (default `Staging`); the GitHub OIDC condition allows only that repository and branch.
  - The CI service account can push to the one registry repository (not project-wide), list Cloud Run jobs (read-only), update the `etl-*` jobs and act as the jobs' service account. Nothing else.
  - `envs/sbx.tfvars`: `github_repository = "Clevrr-AI/clevrr-ai-etl-2.0"`.
- Verify: `terraform apply` in sandbox added 19 resources and changed nothing. Outputs: provider `projects/1004856324034/locations/global/workloadIdentityPools/etl-v2-github/providers/github`, service account `clevrr-etl-v2-ci@clevrr-ai-sbx.iam.gserviceaccount.com`.
- Next (the engineer): set the repository variables `GCP_WIF_PROVIDER` and `GCP_CI_SERVICE_ACCOUNT`, then push to `Staging`. `ETL_IMAGE`, `GKE_CLUSTER` and `GKE_ZONE` have sandbox defaults in `ci.yaml` (added after the first deploy pushed the image and then failed at the GKE step with the two variables empty).

### 2026-10-19 · Cloud Run Jobs, Workflow, Scheduler and CI deploy (Phase 7.5, 7.7)
- Phase: 7.5, 7.7 · Area: infra, CI, cli · Type: new · Risk: medium (creates GCP resources when applied; not applied)
- Changed:
  - `infra/` (terraform, google provider ~> 6, GCS state by partial config):
    - 12 Cloud Run Jobs, one per pipeline plus `etl-pg-migrate`, `etl-ch-migrate` and `etl-sweep`. Each has its own memory and timeout, 1 retry, `task_count` org sharding, the VPC connector `clevrr-vpc-sbx-sql`, and passwords from Secret Manager by name. They carry the label `app=clevrr-etl-v2`, and CI moves their image tag.
    - The `etl-v2-nightly` Workflow: migrate, then catalog, sales, Meta ‖ Meta region ‖ Google, costs, reliability. Each step waits for its execution; a failure stops the chain.
    - Cloud Scheduler for the nightly chain and the sweeper (every 30 min), only when `nightly_schedule` is set.
    - Least-privilege service accounts: jobs, workflow, scheduler.
    - Optional Workload Identity Federation for one GitHub repo, main only.
    - `envs/sbx.tfvars` mirrors the sandbox v2 API's network: Postgres 10.80.112.3, the ClickHouse ILB 10.160.0.8, the same secrets, the pilot-org allowlist, and no schedule.
  - `etl pg-migrate` (Alembic from the image).
  - CI `deploy` job: on main, when the repo variables are set, keyless auth, push `:<sha>`, update every labelled job.
- Why: plan 7.5 and 7.7. No orchestrator product; the chain is a Workflow.
- Files: infra/*, .github/workflows/ci.yaml, etl/cli.py, CLAUDE.md
- Verify: `terraform validate` succeeds and `terraform fmt` is clean (Terraform 1.9.8); the workflow template renders with Workflows expressions intact. `ci.yaml` parses. `docker build` works and `etl list` in the image shows 9 pipelines. `etl pg-migrate` against sandbox `etl_v2` applies nothing (already at head). ruff, pyright strict and the rules are clean; tests: 61 pass.
- Applied in sandbox on 2026-10-19, with the engineer's go:
  - State bucket `gs://clevrr-ai-sbx-tfstate` (versioned, public access blocked).
  - Image `clevrr-etl-v2:manual-20261019` in `clevrr-registry-sbx`.
  - `terraform apply`: 40 resources, no schedules, no CI access. The first apply failed on the workflow ("Workflows service agent does not exist"); `gcloud beta services identity create --service=workflows.googleapis.com` fixed it. Workflow branch names had to be unique across the whole workflow.
  - Test: `gcloud run jobs execute etl-costs --wait` succeeded (1 task, exit 0). All 3 pilot orgs succeeded, with the same rows as local runs (530 / 0 / 1,814).
  - Idle cost is about ₹3 a month, for the image.

### 2026-10-19 · Port the data reliability report (Phase 7.4)
- Phase: 7.4 · Area: checks · Type: port (copied from legacy) · Risk: low (reports only)
- Changed:
  - New pipelines `reliability-shopify`, `reliability-meta` and `reliability-google`, one step `report`, for each IST day of the window (default: yesterday).
  - Each report has the pipeline counts (Shopify API, raw, Postgres, ClickHouse) and each metric from raw, Postgres and ClickHouse, with legacy's verdict (PASS within 0.1%).
  - `etl/checks/sql_reliability*.py` hold legacy's `PIPELINE_COUNT_CONFIG` and `CONNECTOR_METRICS`, copied unchanged (0 differences in the SQL text). Its f-strings are written as concatenation. `ch_query` binds legacy's ClickHouse placeholders.
  - Results are kept in the step detail. An org with a FAIL or ERR verdict is posted to Slack (`alerts.post_message`).
  - Like legacy, the report never fails a run; the pipelines' own check steps do that. Legacy's dashboard stage is not ported, because it calls the backend's metric engine.
- Files: etl/checks/{reliability,sql_reliability,sql_reliability_shopify}.py, etl/core/alerts.py, etl/pipelines.py; local only: tests/unit/test_reliability.py
- Verify:
  - ruff, pyright strict, import contracts and the rules are clean. Tests: 61 pass.
  - Sandbox, shapercult.com: Shopify 2026-09-15 is PASS on all 8 metrics; orders are 141 at every stage, including the Shopify API count. Meta 2026-09-25 is PASS on all 4 metrics, with 311 rows at every stage.

### 2026-10-19 · Port the demo data generator (Phase 7.3, source 6)
- Phase: 7.3 · Area: tools/demo · Type: port · Risk: low (manual tool, demo org only)
- Changed:
  - `tools/demo/`: legacy's generators (`config.py`, `shopify_generator.py`, `meta_ads_generator.py`, `delhivery_generator.py`) copied unchanged and excluded from ruff.
  - `run.py` is the v2 loader through the ETL core: Postgres upserts on legacy's keys, then a ClickHouse replace.
  - ClickHouse replace scope: the org's catalog in backfill; for daily tables, the span the rows cover (refunds are dated after their orders); for shipments, by waybill (the generator stamps them with the run time).
  - Changed from legacy:
    - Rerun-safe: legacy re-appended its whole history daily.
    - Seeded (`--seed`, default 42).
    - `product_cost_history` ids come from the sequence (legacy's 1..N collided with other orgs' ids).
- Files: tools/__init__.py, tools/demo/*, pyproject.toml
- Verify: ruff, pyright and the rules are clean. Tests: 58 pass. Sandbox (getclevrr.com, 2026-10-01..02, run twice into `etl_v2` / `clevrr_db_v2`): one copy of every row (orders 342, order_lines 663, refunds 22, refund_lines 22, advertising 78, shipments 172, shipment_scans 40).
- Note: pixel jobs are not ported here. They move to clevrr-backend-2.0 as workers (decision 2026-10-19).

### 2026-10-19 · Port Google Ads (Phase 7.3, source 5)
- Phase: 7.3 · Area: sources/google_ads, core · Type: port (copied from legacy) · Risk: medium (not run live yet)
- Changed:
  - New pipeline `google-ads`:
    - Refresh the OAuth token per run, in memory only (legacy saved it back to `organization_connectors`).
    - Expand a manager account to at most 25 leaf accounts.
    - Fetch legacy's nine datasets per customer: 30-day chunks, 7-day for product performance.
    - Load the click map for the last 3 days straight to ClickHouse `google_click_map`.
    - Run `sql/google_advertising.sql`, `google_ad_product_mapping.sql` and `google_product_performance.sql`.
    - ClickHouse delete-then-insert for `advertising` (run days), `ad_product_mapping` (org) and `google_ads_product_performance` (run days), then `check_google`.
  - Files: `api.py` (searchStream with legacy's incremental parser, 6 retries at 1.6^n, the 403 retry without login-customer-id), `datasets.py` (GAQL, mappings and row builders copied), `fetch.py`, `pipeline.py`, `sql_google.py`.
  - Settings: `google_ads.client_id` / `client_secret` / `developer_token`.
  - `pg_load.upsert(conflict=...)` for expression unique indexes.
- Files: etl/sources/google_ads/*, etl/core/{settings,pg_load}.py, etl/pipelines.py; local only: tests/unit/test_google_ads.py
- Verify: ruff, pyright strict and the rules are clean. Tests: 58 pass, covering the stream parser across chunk splits, chunking, legacy row shapes and the click map, and token refresh including `invalid_grant`. Not run on sandbox yet: you chose to skip the live run.

### 2026-10-19 · Port Meta Ads (Phase 7.3, source 4)
- Phase: 7.3 · Area: sources/meta · Type: port (copied from legacy) · Risk: medium (live Meta reads, shapercult.com)
- Changed:
  - New pipeline `meta-ads`, per ad account in legacy's order: insights (fatal), creatives for the ads seen in insights, then structure (both non-fatal, recorded in the step detail). Then `sql/meta_advertising.sql`, `meta_ad_product_mapping.sql` and `meta_campaign_structure.sql`. ClickHouse: `advertising` replaces the run's days; the other two replace the org's Meta rows. `check_meta` compares raw vs Postgres vs ClickHouse.
  - New pipeline `meta-ads-region` (its own watermark, as legacy's `ads_region`): spend by region, then `sql/meta_ad_geo.sql`, then a ClickHouse span replace, then `check_geo`.
  - Files: `graph.py` (legacy retries, cursor paging, month chunks, 37-month clamp, split on 500), `creatives.py` (5 phases, no Creative Intelligence jobs), `raw.py` (legacy loader rows), `fetch.py`, `pipeline.py`, `sql_meta.py`, `india_states.py` (copied).
  - Shared: `pg_load.upsert(keep_existing=...)` and `checks.compare.close_figures`.
  - Alembic `0004_meta_creative_links` adds `call_to_action` and `post_link`. Legacy's repo SQL reads them, but the sandbox table lacks them.
- Files: etl/sources/meta/*, alembic/versions/0004_meta_creative_links.py, etl/core/pg_load.py, etl/checks/compare.py, etl/sources/shopify/sql_sales.py, etl/pipelines.py; local only: tests/unit/test_meta.py
- Verify:
  - ruff, pyright strict, import contracts and the rules are clean. Tests: 54 pass.
  - Sandbox, shapercult.com, 2026-09-25..27: both pipelines succeeded with their checks.
  - Against legacy: the same 930 ad-days, none missing or extra. Revenue and purchases are equal; spend is +0.06% because Meta revised the days later. Status differs on ads paused since.
  - v2 finds 547 product links that legacy's older deployed code missed, and none the other way.
  - Region spend (₹436,125.91) matches ad spend (₹436,122.84); legacy's region table holds only ₹130,462 for those days.

### 2026-10-19 · Port Shopify sales (Phase 7.3, source 3)
- Phase: 7.3 · Area: sources/shopify, core, clickhouse · Type: port + fix · Risk: medium (live Shopify reads, pilot orgs)
- Changed:
  - New pipeline `shopify-sales` with five steps:
    - `extract_sales`: incremental runs fetch orders updated since the window start; backfills fetch orders created in the window, in 30-day chunks.
    - `transform_sales`: the six `sql/sales_*.sql` files, ported from legacy by rule.
    - `sync_sales`: append to the ClickHouse ReplacingMergeTree tables; replace the org's Shopify `ad_product_mapping`.
    - `refresh_order_mv`: `sql/ch_order_mv.sql`.
    - `check_sales`: raw vs Postgres vs ClickHouse for the run's orders.
  - The POS rule applies to every order (legacy's updated_at pass dropped the org rule).
  - Raw-to-raw joins match organization_id.
  - `order_mv` reads its sources with FINAL and refreshes every order the run touched; legacy refreshed only orders created that day, so late refunds never arrived.
  - `postgres.session_timezone` (Asia/Kolkata) is set on every connection, as legacy's `utils/db.py` did.
  - Shared `shop.py` client opener.
  - ClickHouse `0002_order_mv_replicated` and `0003_order_sharding`.
  - The migration runner skips `-- cluster only` statements on a single server, and skips only comment-only parts.
- Why (found on sandbox):
  - Without the session time zone, every order was stored 5.5 h early.
  - Legacy's `order_mv_shard` is not replicated, so its two replicas held different rows (56,694 vs 51,863).
  - `order_click_attribution` and `order_mv` were sharded by `rand()`, so a re-synced order landed on the other shard and showed twice. `check_sales` failed the incremental run on this (624 rows for 424 orders).
- Files: etl/sources/shopify/{sales,sql_sales,raw_sales,shop}.py, etl/sources/shopify/sql/{sales_*,ch_order_mv}.sql, etl/core/{settings,db,ch,ch_writer,ch_migrate}.py, clickhouse_migrations/{0002,0003}_*.sql, pyproject.toml; local only: tests/unit/test_shopify_sales.py, tests/integration/{test_shopify_sales,test_clickhouse}.py
- Verify:
  - ruff, pyright strict, import contracts and the rules are clean. Tests: 49 pass.
  - Sandbox (legendoftoys.com, shapercult.com): the September backfill and two incremental runs succeeded with `check_sales` passing.
  - Legacy's current repo SQL, run read-only on the same raw data, gives exactly v2's rows: orders 6,447/6,447 and 5,939/5,939, refunds 391/391 and 654/654, no column differs.
  - Order lines equal legacy's tables. `order_mv` gross and discounts equal the Postgres order lines to the paisa.
- Parity notes:
  - Legacy's sandbox orders differ from its own repo SQL in source classification and the `utm_*_raw` columns (NULL) because its deployed image is older than the repo. Legacy also misses `shipping_refund` values.
  - The other differences are orders and refunds created or edited after legacy's last run (2026-10-01).
  - Click attribution and `ad_product_mapping` need `advertising`, so they wait for the Meta and Google ports.

### 2026-10-17 · Port the Shopify catalog (Phase 7.3, source 2)
- Phase: 7.3 · Area: sources/shopify, core · Type: port + fix · Risk: medium (live Shopify reads, pilot orgs)
- Changed:
  - New pipeline `shopify-catalog` (`etl/sources/shopify/`) with four steps:
    - `extract_catalog`: full pulls of products, custom and smart collections, and collects, in one transaction. Rows Shopify no longer returns are deleted after a complete fetch.
    - `transform_catalog`: runs `sql/catalog_products.sql`, `catalog_variants.sql`, `catalog_prune.sql` and `catalog_cost_seed.sql`.
    - `sync_catalog`: replaces the org's ClickHouse `products` and `product_variants`.
    - `check_catalog`: compares raw, Postgres and ClickHouse.
  - `client.py` is the Shopify REST client. It uses legacy's requests and retries, fails the step at the page cap and on 401 or 403, and gets a client-credentials token in memory only.
  - `etl/core/ch_types.py` converts values to ClickHouse column types, as legacy's Parquet load did (a NULL in a non-Nullable column becomes the default). `ChWriter.append` uses it for every table.
  - Alembic `0003_raw_products_key` keys `raw_shopify_products` on `(id, organization_id)`.
  - httpx request logs are off.
- Why (legacy hazards):
  - The daily wipe was committed before the fetch, so a failed fetch left an org with no catalog. It also erased manual costs.
  - The cost seed closed manual cost rows, replacing them with the selling price.
  - Collects were fetched for the day before only.
  - The 1000-page cap stopped silently.
  - `raw_shopify_products` was keyed on `id` alone.
- Fixes kept in v2:
  - A product's cost follows Shopify until the app sets it by hand, then stays.
  - Variant manual costs stay.
  - The seed closes only `shopify_price` rows.
  - An empty products response fails the step instead of deleting the catalog.
- Files: etl/sources/shopify/*, etl/core/{ch_types,ch_writer,logging}.py, etl/pipelines.py, alembic/versions/0003_raw_products_key.py, CLAUDE.md; local only: tests/unit/{test_shopify_client,test_ch_writer}.py, tests/integration/test_shopify_catalog.py
- Verify:
  - ruff, pyright strict, import contracts and the rules are clean. Tests: 42 pass. Integration tests on a fake shop cover end-to-end with a rerun, manual costs kept while Shopify changes follow, pruning, the empty-catalog guard, and another org untouched.
  - Sandbox: `0003` applied to `etl_v2`. Live runs succeeded twice for legendoftoys.com (93 products, 401 variants) and shapercult.com (65 / 1,750). `check_catalog` passed, the cost seed stayed at one open row per variant, and the structure of `public` and `clevrr_db` did not change.
- Parity with legacy (last legacy catalog run 2026-10-01):
  - shapercult.com: the same products, variants, collects and collections. Title, status, price, collections, cost and created_at agree on all 65 products. updated_at differs on the 49 edited since, and inventory has changed.
  - legendoftoys.com: 12 new products since then, none missing; the other differences are price edits and updated_at.
  - Legacy ClickHouse is short: 75/81 and 61/65 products.
- Open: mydesignation.com fails at the token request (Shopify answers HTTP 400 to its client credentials). Legacy's last sandbox runs for this org also failed. The sandbox connector needs new credentials.

### 2026-10-17 · Port the costs mirror (Phase 7.3, source 1)
- Phase: 7.3 · Area: sources/costs, core, checks · Type: port + fix · Risk: low
- Changed:
  - New pipeline `costs` (`etl/sources/costs/mirror.py`) with steps `mirror_product_costs`, `mirror_variable_costs` and `check_costs`.
  - It reads `public.product_cost_history` and `public.variable_cost_rules_versions`, because the app writes manual costs and the variable rules there. It replaces each org's rows in ClickHouse, then compares rows, distinct ids and totals with Postgres.
  - `etl/checks/compare.py` holds the shared compare.
  - `ChWriter` now inserts with `insert_deduplicate=0`, and on a cluster with `insert_quorum='auto'`. It writes naive datetimes as UTC.
  - `scripts/check_rules.py` allows reads of the two cost tables in `public`. The new rule `public-write` refuses any write, create or alter on a `public.` table.
- Why:
  - Legacy appended each org's full cost history every night with no delete. Sandbox held 17,840 rows for 269 ids (legendoftoys.com), and shapercult.com was never mirrored.
  - The first v2 rerun on sandbox lost 9 rows. A Replicated table drops a block whose hash it saw recently, even after a delete. `check_costs` failed both orgs, so the loss was not silent.
  - clickhouse-connect reads a naive datetime in the machine's zone (IST on a laptop).
- Files: etl/sources/costs/*, etl/checks/compare.py, etl/core/ch_writer.py, etl/pipelines.py, scripts/check_rules.py, CLAUDE.md; local only: tests/unit/test_costs.py, tests/unit/test_ch_writer.py, tests/integration/test_costs.py
- Verify:
  - ruff, pyright strict, import contracts and the rules are clean. Tests: 33 pass.
  - Sandbox (pilot orgs, 3 reruns after the fix): every run succeeded. `clevrr_db_v2.product_cost_history` equals Postgres for each org in rows, ids and cost total (legendoftoys.com 530, shapercult.com 1,814, mydesignation.com 0). Both replicas of each shard agree.
  - The structure hashes of `public` and `clevrr_db` did not change.
- Note: legacy deletes and reinserts products and variants in ClickHouse with the default dedup. Its daily catalog wipe changes the timestamps, which probably hides the same loss. The v2 catalog keeps rows stable, so it depends on `insert_deduplicate=0`.

### 2026-10-17 · Create the sandbox targets `etl_v2` and `clevrr_db_v2`
- Phase: 7.6 · Area: schema · Type: ops · Risk: low (new schema and database only; the engineer approved it)
- Changed:
  - `alembic upgrade head` (`POSTGRES__TARGET_SCHEMA=etl_v2`) created schema `etl_v2` in the sandbox `clevrr_db`, with 44 tables.
  - `etl ch-migrate` (`CLICKHOUSE__DATABASE=clevrr_db_v2`, `CLICKHOUSE__CLUSTER=sbx-cluster`) created `clevrr_db_v2` on all 4 sandbox replicas, with 26 Distributed tables over their `_shard` tables, plus `ch_migrations`.
- Why: Parity runs write here, never to legacy's tables.
- Verify:
  - The structure hashes of `public` (132 tables) and `clevrr_db` (52 tables) were the same before and after.
  - No v2 replication path is shared with `clevrr_db`, and every replica is healthy. Paths are under `/clickhouse/tables/{shard}/clevrr_db_v2/`; one table keeps its `{uuid}` path.
  - A rerun of both commands applied nothing.
- Tunnels: `cloud-sql-proxy clevrr-ai-sbx:asia-south1:clevrr-psql-db-sbx --port 55433 --gcloud-auth` and `kubectl port-forward -n clickhouse svc/sbx-ch-ilb 18124:8123 --context=gke_clevrr-ai-sbx_asia-south1-a_clevrr-ckh-cluster-sandbox`. The users are `postgres` and `app`. The passwords are the Secret Manager secrets `v2-postgres-password` and `v2-clickhouse-password` (project `clevrr-ai-sbx`).

### 2026-10-17 · Add the data-table migrations and the writers (Phase 7.6, foundations for 7.3)
- Phase: 7.6, 7.3 · Area: core, schema · Type: new · Risk: low (nothing ran on sandbox)
- Changed:
  - Alembic `0002_data_tables` creates the 40 ETL-owned Postgres tables from the backend's sandbox baseline. `scripts/pg_tables.py` generates it. It has no `public.` prefixes and no foreign keys, and every constraint is idempotent.
  - `clickhouse_migrations/0001_baseline.sql` has 26 tables, each a `_shard` table plus a Distributed table. `scripts/ch_baseline.py` generates it from the sandbox dump. It uses `${db}` and `${cluster}`. Replicated paths are `/clickhouse/tables/{shard}/${db}/<table>`, so `clevrr_db_v2` never shares a path with legacy.
  - `etl ch-migrate` applies the files and records them in `ch_migrations`. On a single server (local, tests) it renders the same file without ON CLUSTER, Distributed, replication or TTL.
  - `ChWriter` has `append` and `replace`. `replace` deletes the org's slice (`<t>_shard ON CLUSTER`, `mutations_sync=2`), checks that the slice is empty, then inserts.
  - `pg_load.upsert` uses COPY to a temp stage, then one `INSERT ... ON CONFLICT`. When a key repeats in a batch, the last row wins.
  - `pg_load.run_sql_file` runs transform files with bound values.
  - The ClickHouse cluster setting accepts a hyphen (`sbx-cluster`).
- Why: Legacy runs one INSERT per row, pastes `${org_id}` into SQL text, reads every org's rows to write one org, and its replicated paths hard-code `clevrr_db`.
- Files: alembic/versions/0002_data_tables.py, alembic/sql/0002_data_tables.sql, clickhouse_migrations/*, etl/core/{ch,ch_migrate,ch_writer,pg_load,settings}.py, etl/cli.py, scripts/{pg_tables,ch_baseline,check_rules}.py; local only: tests/
- Verify:
  - ruff, pyright strict, import contracts and `scripts/check_rules.py` are clean.
  - Tests: 24 pass.
  - On ClickHouse 24.8: the baseline renders single-node and a rerun applies nothing. The cluster form keeps ON CLUSTER, Distributed and `${db}` paths, with no `clevrr_db` left. `replace` deletes only one org's slice. Unsafe identifiers are refused.
  - On Postgres 16: the 40 tables land in `etl_v2`. Upsert inserts, updates, keeps the last duplicate, writes jsonb, and runs twice in one transaction. A SQL file binds values, so an injected quote stays a value.

### 2026-10-17 · Scaffold the repo and the step model (Phase 7.1, 7.2)
- Phase: 7.1, 7.2 · Area: core, tooling · Type: new · Risk: low
- Changed: New repo layout, tooling (uv, ruff, pyright strict, import-linter, rule checks, pre-commit, CI), settings, logging, database clients, the step model (`Pipeline`, `Step`, `Window`, `Org`, `StepResult`, `StepFailed`), the runner, the run ledger with heartbeat and sweeper, alerts, the internal API client and the `etl` command. Alembic `0001_run_ledger` creates `etl_runs` (legacy columns plus `pipeline`, `heartbeat_at`, `finished_at`, `execution`, `task_index`), `etl_steps` and `etl_watermarks` in the target schema; on `public` it only adds what is missing.
- Why: Phase 7 starts. Legacy left runs `running` forever, advanced watermarks on partial runs and swallowed fetch failures.
- Files: pyproject.toml, uv.lock, etl/core/*, etl/runner.py, etl/pipelines.py, etl/cli.py, alembic/*, scripts/check_rules.py, Dockerfile, .github/workflows/ci.yaml, CLAUDE.md; local only: tests/
- Verify: ruff, pyright strict, import contracts and `scripts/check_rules.py` are clean. Tests: 16 pass. Unit (13): windows, step order, stop on first failure, other orgs go on, the watermark only after the last step of an incremental run, a backfill never moves it, SIGTERM records `killed`, sharding, the allowlist, alerts, the internal API client (OIDC token, retries), the write-target rules. Integration on Postgres 16 (3): the migration on a fresh `etl_v2` and on `public` with legacy's `etl_runs` (its row and columns untouched), the run/step/watermark flow (never backwards), unqualified writes reach `etl_v2` only, the sweeper kills a stale run and its step, the heartbeat keeps a live run. `docker build` works and `etl list` runs in the image.
