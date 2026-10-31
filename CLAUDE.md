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
- `etl/scheduler.py`: `etl tick <source>`, the per-org sync frequency (`etl_schedules`, `etl/core/schedules.py`; `docs/scheduling.md`).
- `alembic/` (version table `alembic_version_etl`), `clickhouse_migrations/`, `infra/`, `tests/`.

## Table ownership

- This repo owns the data tables (raw_*, orders, order_lines, refunds, products, ad metrics, shipments, costs) and the run ledger (`etl_runs`, `etl_steps`, `etl_watermarks`) through its Alembic, and all ClickHouse DDL through `clickhouse_migrations/`.
- `clevrr-backend-2.0` owns the application tables (users, organizations, `organization_connectors`, dashboards, credits, chat). This repo reads `organization_connectors` until the internal API exists, and never migrates it.

## Rules

1. A step is `def step(ctx, org, window) -> StepResult`. It is idempotent: a rerun of the same window leaves the same rows. It reads no environment variables and keeps no module state.
2. Read environment variables only in `etl/core/settings.py` and `etl/legacy.py` (the legacy-settings boundary for the existing CronJobs). Give no secret a default.
3. Do not build SQL with f-strings, `.format()`, `%` or `+`. Pass values as parameters (`%(name)s` for Postgres, `{name:Type}` for ClickHouse). Take identifiers from `psycopg.sql` or fixed constants. Put a query builder in a file named `sql_*.py`.
4. Write only the target schema and database (settings), never a hard-coded one. Use unqualified table names. Name `public.<table>` only to read a table the backend owns (`organizations`, `organization_connectors`, `connectors`, `connector_endpoints`, `utm_rules`, `utm_mappings`, `attribution_channel_rules`). Never name `clevrr_db.`. `scripts/check_rules.py` enforces this.
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
- Commands written for the engineer to paste run in PowerShell, where the kube context and the gcloud project are prod: give `--context=` and `--project=` inline on every line, never through a shell variable, and never bash syntax.

## Decisions (2026-10-17)

- Credentials: the ETL reads `public.organization_connectors` behind `etl/core/connectors.py` (`ConnectorSource`). It moves to the backend's internal API (`/internal/connectors*`, ADR-0003) after the agent phase, without a change to the pipelines.
- Targets (changed 2026-10-19 by the TL): in sandbox, v2 writes the same tables as legacy, `public` and `clevrr_db`; the separate `etl_v2` / `clevrr_db_v2` targets are retired. Legacy's sandbox CronJobs are suspended, so v2 is the writer there; `ckh-etl-ondemand` (legacy image) still writes on connect. The `etl legacy` guard still refuses `public` / `clevrr_db` unless `ETL_V2_CUTOVER=true`, which sandbox sets and prod does not. Every migration must stay compatible with legacy's loaders (no key changes legacy's `ON CONFLICT` cannot use; no data dropped). Applied on the sandbox 2026-10-19: `etl pg-migrate` on `public`, `etl ch-migrate` on `clevrr_db`, `ETL_V2_CUTOVER=true` in `etl-config`. The retired `etl_v2` schema and `clevrr_db_v2` database were dropped on 2026-10-21 (the engineer's go).
- Live provider calls: the pilot orgs only (`legendoftoys.com`, `shapercult.com`, `mydesignation.com`), read-only, with their sandbox credentials.
- Settings use nested names with a double underscore (`POSTGRES__HOST`), as in clevrr-backend-2.0: one name per setting, no aliases.
- Amazon Ads is not ported (decision 2026-10-17): the product switched it off. Legacy's `etl-all-amazon-ads` CronJob keeps its own schedule until legacy is retired.
- Delhivery is deferred (decision 2026-10-17): port it after the other sources. Legacy keeps its own CronJob until then.
- Google Ads: verified by tests only; no live run yet (decision 2026-10-19). Its OAuth client is `GOOGLE_ADS__CLIENT_ID` / `GOOGLE_ADS__CLIENT_SECRET`; legacy keeps them in the sandbox ConfigMap `etl-config`.
- Pixel jobs (attribution, probabilistic stitch, conversion export) are not ETL pipelines: they move to clevrr-backend-2.0 as v2 workers, next to pixel ingest (decision 2026-10-19).
- Runtime (decision 2026-10-19): the EXISTING `etl-all-*` CronJobs in the existing GKE cluster (namespace `etl`), unchanged except the image: their files are migrated into `cronjobs_dev/` (sandbox) and `cronjobs/` (prod, never applied during parity). The v2 image runs their `ETL_*` settings through `etl legacy`, with the Cloud SQL proxy inside the image as legacy had it. `infra/` keeps the GCP side only: the two migrate Cloud Run Jobs (`etl-pg-migrate`, `etl-ch-migrate`), the CI identity and CI's GKE read access; the one-job-per-pipeline Cloud Run Jobs, the nightly Workflow and the schedulers were removed on 2026-10-21 (the engineer's decision).
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

### 2026-10-31 · `etl-ai-overview` applied and unsuspended on the sandbox (pilot orgs)
- Phase: 7.5 · Area: cronjobs_dev, sandbox cluster config · Type: change · Risk: low (one API call a night for three orgs)
- Changed: `cronjobs_dev/etl-ai-overview.yaml` now carries the CI image tag `b122bf68...` (the first image with the `ai-overview` command) and `ETL_COMMAND=ai-overview --org shapercult.com --org legendoftoys.com --org mydesignation.com`: the sandbox API still runs tasks inline, so every org named runs inside the one trigger request, and three is the limit that fits. Cluster: `etl-config` got `API__BASE_URL=https://api-sbx.getclevrr.com` and `API__AUDIENCE=https://api-sbx.getclevrr.com`; the CronJob was applied and unsuspended (`0 1 * * *` UTC). The API allows the CronJobs' Workload Identity account `gke-etl-cloudsql@clevrr-ai.iam.gserviceaccount.com` (backend-2.0 change log, 2026-10-31).
- Why: The user asked for the ETL settings and the CronJob on sandbox.
- Files: cronjobs_dev/etl-ai-overview.yaml, CLAUDE.md
- Verify: a one-off Job `etl-manual-20261031-040010` (`ai-overview --force --org shapercult.com`) reached the API with its OIDC token: the Cloud Run log shows 202s and the API wrote the org's three cards. The Job itself exited 1 with `ApiError`: the API ran the org inline for 63 to 103 s, over the client's 20 s timeout, so the three retries on two pods made six runs of the same org. Fixed on the API side by moving its tasks to Cloud Tasks (backend-2.0; needs three IAM grants an owner must make). Until then do not widen the sandbox `--org` list. Not checked: the scheduled 01:00 UTC fire.

### 2026-10-31 · `etl ai-overview`: the nightly trigger of the API's AI Overview job
- Phase: 7.5 · Area: cli, core (api_client), legacy, cronjobs · Type: feature · Risk: low (one API call a night; no Postgres or ClickHouse writes of its own)
- Changed: (1) `etl ai-overview [--day YYYY-MM-DD] [--org ID ...] [--force]` calls backend 2.0's `POST /api/v2/internal/ai-overview/run` with the job's OIDC token (`InternalApi.ai_overview_run`, the same `API__BASE_URL` / `API__AUDIENCE` as the landed signal). The API picks the orgs (data sources, users active in the last 4 days) and queues one worker job per org that writes the morning's AI Overview, Sales metrics and Ads-that-need-attention cards; the command logs the counts queued and skipped and exits 1 when the API settings are unset or the call fails. (2) `etl legacy` passes `ai-overview` through like the other v2 commands. (3) CronJobs `etl-ai-overview` (`cronjobs_dev/`, `cronjobs/`): `0 1 * * *` UTC = 06:30 IST, after the 00:30 IST daily ticks, `suspend: true`, small resources. Docs: `docs/scheduling.md` (new section), `cronjobs_dev/README.md`.
- Why: The user wants the AI Overview computed in a nightly job and read in the morning; the ETL knows when the night's data has landed, the API owns the analysis (clevrr-backend-2.0 change log, 2026-10-31).
- Files: etl/cli.py, etl/core/api_client.py, etl/legacy.py, cronjobs_dev/etl-ai-overview.yaml, cronjobs/etl-ai-overview.yaml, docs/scheduling.md, cronjobs_dev/README.md, CLAUDE.md; local only: tests/unit/test_ai_overview_cmd.py
- Verify: ruff, pyright, lint-imports and check_rules pass on `etl/`; unit `tests/unit/test_ai_overview_cmd.py` and `test_legacy.py` 12 passed (the request body and bearer token against a mock transport, the legacy passthrough, exit 1 without `API__*`, exit 0 with the counts, exit 1 on a 503). Not checked: a run against the sandbox API (the API needs its `0007_ai_overview` migration and the ETL service account in `INTERNAL__ALLOWED_SERVICE_ACCOUNTS`).
- To apply: set `API__BASE_URL` and `API__AUDIENCE` in `etl-config` (if not yet set for the landed signal), `kubectl apply -f cronjobs_dev/etl-ai-overview.yaml`, unsuspend it after the API is deployed; a first manual run through `scripts/manual_job.py "ai-overview --force --org shapercult.com"`.

### 2026-10-30 · Per-org sync frequency: `etl tick`, `etl schedule`, the etl-tick-* CronJobs
- Phase: 7.5 · Area: cli, core (schedules, run_ledger, connectors), scheduler, legacy, meta, google_ads, cronjobs · Type: feature · Risk: medium (Meta and Google incremental windows now include today and a 3-day look-back; the nightly jobs are replaced by hourly ticks)
- Changed: (1) `etl_schedules` (Alembic 0007): one row per (org, source) with `interval_minutes` in 60..1440 (divisors of a day), `enabled`, `note`; no row = daily. `etl schedule list|set|unset` manages it. (2) `etl tick <source>` (`etl/scheduler.py`) runs the source's pipelines (`shopify-sales`; `meta-ads`, `meta-ads-region`; `google-ads`) for the orgs whose IST-midnight-anchored slot has started since their last successful run (`etl_watermarks.last_run_at`, new `Ledger.last_run_at`). The `etl-tick-*` CronJobs fire every hour (`0 * * * *` UTC, :30 IST); a daily org still runs once a night at 00:30 IST, a 4-hour org at 00:30, 04:30, ... (3) Meta and Google `days(mode, window)`: an incremental run includes the day the window ends in (today, partial) and both pipelines re-pull a 3-day look-back; a backfill's `--to` stays exclusive. (4) `etl legacy` passes a v2 `ETL_COMMAND` (`tick ...`, `schedule ...`, `pg-migrate`) through, so the new CronJobs and the one-off `cronjobs_dev/etl-manual.yaml` Job (`scripts/manual_job.py`) share `etl-config`. (5) `select_orgs` moved to `etl/core/connectors.py`; the tick path sends the landed signal like `etl run`.
- Why: The TL wants the sync frequency configurable per org (hourly, 4h, 6h, daily). Shopify already worked at any interval (updated_at_min, 6 h look-back); Meta and Google never fetched today and had no look-back, so an intraday run did nothing. Research and limits: `docs/scheduling.md`.
- Files: etl/scheduler.py, etl/core/schedules.py, etl/core/run_ledger.py, etl/core/connectors.py, etl/cli.py, etl/legacy.py, etl/sources/meta/pipeline.py, etl/sources/google_ads/pipeline.py, alembic/versions/0007_etl_schedules.py, cronjobs_dev/etl-tick-*.yaml, cronjobs_dev/etl-manual.yaml, cronjobs/etl-tick-*.yaml, scripts/manual_job.py, docs/scheduling.md, cronjobs_dev/README.md, .github/workflows/ci.yaml
- Verify: ruff, ruff format, pyright, lint-imports, check_rules pass; 56 unit tests pass (slots, dueness, tick runs only the due orgs, legacy passthrough, days() in both modes). Sandbox, 2026-10-30 (image `manual-20261030-sched2`, built from this tree on top of 00f8b10): `pg-migrate` ran 0006 -> 0007; `schedule set` shapercult.com shopify 4h and meta_ads 4h; a manual `tick shopify` at 16:24 IST decided `due` (last run 08:30 IST, the 16:00 slot had started) and `shopify-sales` succeeded in 32 s; a manual `tick meta_ads` ran `meta-ads` (window 27 Oct 09:00 IST -> now, 4 days incl. today, 9037 insight rows, 2.5 min) and `meta-ads-region` (23520 rows), both succeeded; a second `tick shopify` skipped with `ran this slot`. The `meta.advideos_batch_failed` warnings are pre-existing (8 in that morning's nightly run). Cluster: `etl-tick-shopify` and `etl-tick-meta-ads` unsuspended, `etl-all-shopify` and `etl-all-meta-ads` suspended; `etl-tick-google-ads` stays suspended with `etl-all-google-ads` (no `GOOGLE_ADS__*` in `etl-secrets`). Not checked: a Google Ads tick against the live API; the hourly tick under more than one org.
- To apply: `etl pg-migrate` (0007), `kubectl apply` the etl-tick-* files, set the orgs' intervals with `etl schedule set`, unsuspend the tick jobs and suspend `etl-all-shopify`, `etl-all-meta-ads`, `etl-all-google-ads` (else a daily org runs twice a night).

### 2026-10-27 · The API's landed signal, `success` as the run status, the legacy end date
- Phase: 7.3, 7.8 · Area: cli, core (api_client, model), legacy, runner · Type: change · Risk: low
- Changed: (1) After every `etl run`, `cli._signal_landed` tells backend 2.0 that each succeeded org's data landed: `POST /api/v2/internal/etl/landed` with `{org_id, provider, run_id, mode, window_start, window_end, tables}` (the ADR-0003 contract; the API sets `historic_done`, flips the trial and sends the data-ingested email). Only `shopify-sales`, `meta-ads` and `google-ads` signal (the catalog and the region mirror are extras, as legacy's hook skipped them). The call needs `API__BASE_URL` and `API__AUDIENCE`; unset, nothing is sent. A failed call is a warning: the data is loaded. `OrgOutcome` carries the window and mode on success. (2) `Status.SUCCEEDED` is written as `success` (decision D10): backend 2.0's last-sync read and the website's "Synced N ago" only count that value, so v2 runs never showed as synced. (3) `etl legacy` turns an `ETL_END_DATE` inside a day (`T-1 23:59:59`, what legacy's on-demand trigger sends) into the next midnight for the exclusive `--to`, so the last day is not lost.
- Why: The onboarding port (backend 2.0, 2026-10-27): the first load must set `historic_done` or the data-loading screen waits forever; the two bugs were found while mapping the flow.
- Files: etl/cli.py, etl/core/api_client.py, etl/core/model.py, etl/legacy.py, etl/runner.py, CLAUDE.md
- Verify: ruff, ruff format, pyright, lint-imports and check_rules pass. Not checked: a run against the API (needs the ETL job's service account in the API's `INTERNAL__ALLOWED_SERVICE_ACCOUNTS` and `API__*` on the job), and rows already written as `succeeded` on sandbox (an `UPDATE etl_runs SET status = 'success' WHERE status = 'succeeded'` makes them count).
- To apply: set `API__BASE_URL=https://api-sbx.getclevrr.com` and `API__AUDIENCE=<the API's INTERNAL__AUDIENCE>` on the ETL jobs (etl-config) once the API has the landed route deployed.

### 2026-10-27 · Google Ads: a picture for each ad
- Phase: 7.3 · Area: google_ads · Type: change · Risk: low (the creative snapshot stays best-effort: a failure logs and skips it)
- Changed: The creative snapshot query also selects the image and video assets of display, Demand Gen, app and video ads. One `asset` query per account (IMAGE and YOUTUBE_VIDEO) gives each asset's picture URL or YouTube video id. `datasets.picture` picks one picture per ad: the image ad's URL, else the first marketing image, else the YouTube thumbnail (`i.ytimg.com/vi/<id>/hqdefault.jpg`). The raw table gets `image_url` and `youtube_video_id` (Alembic 0006); `video_ad_video` now holds the video asset name. The advertising transform writes the picture to `thumbnail_url` and `image_url`, like Meta; campaign-level rows get NULL.
- Why: The dashboard's Insights panel shows Google ads now (backend 2.0, 2026-10-27) and the engineer wants their pictures. Search and Shopping ads have no picture in the API.
- Files: etl/sources/google_ads/datasets.py, etl/sources/google_ads/fetch.py, etl/sources/google_ads/sql/google_advertising.sql, alembic/versions/0006_google_ad_pictures.py, CLAUDE.md
- Verify: ruff, ruff format, pyright, lint-imports and check_rules pass; `alembic heads` is 0006. A local run of `creative_row` on sample rows gives the image URL for an image ad, the asset URL for a display ad, the YouTube thumbnail for a video ad and none for a search ad. Not checked: the GAQL field names against the live API v24 (if one is wrong the snapshot fails and logs `google_ads.creatives_failed`), and the migration on a database.
- To apply: `uv run etl pg-migrate` (0005 and 0006), then a Google Ads run; a backfill run fills pictures for old days.

### 2026-10-27 · Google Ads: the ad's serving status (ad_group_ad.primary_status)
- Phase: 7.3 · Area: google_ads · Type: change · Risk: low (one nullable column; a run before the migration fails on the missing column)
- Changed: The ad-performance query reads `ad_group_ad.primary_status` too. The raw row keeps it in the new column `raw_google_ads_ad_group_ad.ad_primary_status` (Alembic 0005). The advertising transform writes `ad_status = COALESCE(NULLIF(ad_primary_status, ''), ad_status)`: the serving status (ELIGIBLE, LIMITED, PAUSED, NOT_ELIGIBLE, PENDING, REMOVED) when the fetch has it, else the user-set status as before. `ad_group_status` and `campaign_status` stay the user-set statuses.
- Why: A Google ad's `ad_status` was the user-set ENABLED, which says nothing about an ad whose ad group or campaign is paused, or an ad that is disapproved. Meta's `ad_status` is already `effective_status`. With the same meaning on both sources, the dashboard's "Active only" can work for Google ads (backend 2.0 and frontend 2.0 count ELIGIBLE and LIMITED as serving, 2026-10-27).
- Files: etl/sources/google_ads/datasets.py, etl/sources/google_ads/sql/google_advertising.sql, alembic/versions/0005_google_ad_primary_status.py, CLAUDE.md
- Verify: ruff, ruff format, pyright, lint-imports and check_rules pass. Not checked: a run against the Google Ads API and the migration on a database (the engineer runs `uv run etl pg-migrate` on sandbox, then a Google Ads run; rows loaded before that keep ENABLED / PAUSED).
- To apply: `uv run etl pg-migrate` on each target schema before the next Google Ads run. A backfill run over the history refreshes `ad_status` for old days; without it only new days carry the serving status.

### 2026-10-21 · Sandbox nightly on, for one pilot org (Phase 7.5, a rehearsal of gate 7)
- Phase: 7.5 · Area: sandbox cluster config · Type: change · Risk: low (sandbox; reversible with `suspend: true`)
- Changed (cluster only; the files are unchanged):
  - `etl-config`: `RUN__ORG_ALLOWLIST` narrowed to one pilot org for the first nights.
  - `etl-products-cost`: the live `ETL_ORG_IDS` pin removed (its file never had it), so the costs mirror follows the allowlist.
  - Unsuspended: `etl-all-shopify-catalog-daily` (18:30 UTC), `etl-products-cost` (02:45), `etl-all-shopify` (03:00), `etl-all-meta-ads` (03:30). Still suspended: `etl-all-google-ads` (no `GOOGLE_ADS__*` in `etl-secrets` yet), `etl-all-shopify-products` (the same run as catalog-daily), `etl-custom-backfill`, Delhivery, Amazon. `startingDeadlineSeconds=600` is set on every job, so unsuspending started nothing; the first runs are the scheduled ones.
  - Watermarks: v2 keys a watermark by (source, pipeline name) — `shopify-sales`, `meta-ads`, `meta-ads-region`, `google-ads` — while legacy used `orders`, `ads`, `ads_region`. Without a row, v2 treats the org as new (`first_window`: 3 days for sales, 365 days for Meta). The org's four v2 rows were seeded from legacy's rows (`INSERT ... SELECT`, `ON CONFLICT DO NOTHING`, mode `incremental`), so the first run continues from legacy's last position. The same step belongs to every cut-over; `cronjobs_dev/README.md` has the SQL.
- Verify: a manual `kubectl create job --from=cronjob/etl-all-shopify` run for the org through the CronJob path succeeded (5½ minutes including the node start; three weeks of orders, 27 pages). All five steps `succeeded`; Postgres and ClickHouse hold the same row counts for orders, order lines, refunds and click attribution; the `shopify-sales` watermark advanced to the window end; `reliability-shopify` for the last day: `failed: 0`. The run-level `records_in` is the sum over the steps (raw rows fetched + rows synced + `order_mv` rows), not an order count.
- Found: the org's `order_mv` holds far fewer orders than `orders` — legacy's sandbox only ever refreshed `order_mv` for the orders a run touched after the table was introduced. Not a v2 change; a one-time backfill run (`--from` the first order) would fill it.
- Next: after the first night, read the org's `etl_runs` / `etl_steps`, then run `reliability-shopify` and `reliability-meta` for it.

### 2026-10-21 · Drop the parity targets and reduce `infra/` to the migrate jobs
- Phase: 7.5, 7.6 · Area: infra, sandbox databases · Type: change · Risk: medium (applied in sandbox)
- Changed:
  - `infra/`: `jobs.tf` keeps `etl-pg-migrate` and `etl-ch-migrate` only (one task each); `workflow.tf` and `nightly.yaml.tftpl` deleted; `iam.tf` keeps the jobs' account and its secret access (the workflow and scheduler accounts, their bindings and the Workflows / Scheduler API records are gone); `variables.tf` loses `task_count` and `nightly_schedule`; `outputs.tf` loses `workflow`; `envs/sbx.tfvars` and the README describe the reduced shape. `ci.yaml` still updates every job labelled `app=clevrr-etl-v2`, now the two migrate jobs.
- Why: the engineer (2026-10-21): the pipelines run in the Kubernetes CronJobs, so the one-job-per-pipeline Cloud Run Jobs, the Workflow and the schedulers were redundant; the parity targets `etl_v2` / `clevrr_db_v2` have been unused since the switch to `public` / `clevrr_db`.
- Files: infra/jobs.tf, infra/iam.tf, infra/outputs.tf, infra/variables.tf, infra/envs/sbx.tfvars, infra/README.md, .github/workflows/ci.yaml, CLAUDE.md; deleted: infra/workflow.tf, infra/nightly.yaml.tftpl
- Verify: `terraform fmt`, `validate` and each plan were read before the applies; the end state is 18 resources (2 jobs with their 2 CI bindings, the etl and CI accounts, WIF pool + provider, registry push, 3 API records, 2 secret bindings, the GKE viewer binding, actAs and WIF bindings).
- Applied in sandbox on 2026-10-21, with the engineer's go:
  - ClickHouse: the empty Distributed shells `order_mv_legacy` and `order_click_attribution_legacy` dropped (the engineer's own attempt ran against another kube context and never reached the sandbox, see the note below); database `clevrr_db_v2` dropped on all four replicas. `order_mv_shard_legacy` and `order_click_attribution_shard_legacy` (the old rows) stay until the engineer is satisfied.
  - Postgres: schema `etl_v2` (44 tables) dropped inside a transaction that would have rolled back had the CASCADE touched anything in `public` (it did not; `public` still has 134 tables). The tunnel's target instance was read from the proxy process's command line first. The first attempt silently rolled back: a SELECT had run on the connection before `with conn.transaction()`, which in psycopg 3 opens an implicit transaction and turns the block into a savepoint, so nothing was committed and closing the connection undid the drop — the schema was still there in pgweb. The second attempt used a fresh connection with the block as the outermost transaction and was verified from separate connections. Rule: on a non-autocommit psycopg connection, run nothing before `with conn.transaction()`, and verify DDL from another connection.
  - Terraform: 0 added, 2 changed (the migrate jobs now carry `public` / `clevrr_db`), 42 destroyed over three applies. The first apply stopped at the Workflow: the google provider refuses to destroy a Workflow unless `deletion_protection = false` was applied first, which would have meant re-adding the deleted resource and its account to flip a flag; the Workflow was removed from the state and deleted with `gcloud workflows delete` instead. The Workflows, Workflow Executions and Cloud Scheduler APIs stay enabled (`disable_on_destroy = false`).
  - Left as it was: `ckh-etl-ondemand` (legacy image). The two `*_shard_legacy` tables (the old order rows) were dropped later the same day by the engineer, from the ClickHouse pod with the lines handed over (query log: 04:47 UTC); `order_mv` and `order_click_attribution` keep every row. ZooKeeper keeps two empty `/clickhouse/tables/{0,1}/clevrr_db_v2` nodes (ClickHouse removes the table nodes, not the database parent); harmless.
- Note on commands handed to the engineer: their shell is PowerShell with the kube context and the gcloud project set to prod. A bash-style `C=--context=...` line fails there and leaves `$C` empty, so a following `kubectl ... $C` runs against prod (it failed only because the sandbox pod name does not exist there). Pass `--context=` and `--project=` inline, never through a variable (working agreement added).

### 2026-10-19 · Switch the sandbox to legacy's own tables, `public` and `clevrr_db` (Phase 7.6, the TL's decision)
- Phase: 7.6, 7.5 · Area: migrations, cronjobs, config · Type: change · Risk: medium (applied in sandbox)
- Changed:
  - `alembic/versions/0003_raw_products_key.py`: a unique index on `raw_shopify_products (id, organization_id)` instead of a new primary key, so legacy's `ON CONFLICT (id)` keeps working on the same table.
  - `clickhouse_migrations/0002`, `0003`: keep the rows. The shard table is renamed to `*_shard_legacy`; the Distributed table is dropped and recreated, because a renamed Distributed table keeps reading its shard table's *name* and would read the new, empty one (the first copy in the sandbox moved no rows for that reason); the new tables are created under the old names and the old rows are copied from every replica (`clusterAllReplicas`: legacy's `order_mv_shard` was unreplicated, so each replica held different rows). `0002` names the columns in the copy.
  - `infra/envs/sbx.tfvars`: `target_schema = "public"`, `database = "clevrr_db"`. Not applied yet: the keep-or-delete decision on the Cloud Run pipeline jobs is open.
  - `clickhouse_migrations/README.md`, `cronjobs_dev/README.md`: describe the applied state (the `etl-config` keys are now `ETL_V2_CUTOVER`, `CLICKHOUSE__CLUSTER`, `RUN__ORG_ALLOWLIST`).
- Why: the TL (2026-10-19, through the engineer): no separate `etl_v2` / `clevrr_db_v2`; v2 writes the sandbox's existing tables. Legacy's sandbox copy is retired; legacy prod is not touched.
- Files: alembic/versions/0003_raw_products_key.py, clickhouse_migrations/0002_order_mv_replicated.sql, clickhouse_migrations/0003_order_sharding.sql, clickhouse_migrations/README.md, cronjobs_dev/README.md, infra/envs/sbx.tfvars, CLAUDE.md; local only: tests/integration/test_clickhouse.py, tests/integration/test_shopify_catalog.py
- Verify: ruff, the rules and the unit tests are clean; the integration tests pass (19). The ClickHouse test renders both migrations (5 cluster statements, 3 of them cluster-only, with the drop and the `clusterAllReplicas` copy; 2 statements on a single server). The catalog test gave a second org its own product id: it had reused id 1, which the kept `PRIMARY KEY (id)` rejects, and Shopify ids are global (the sandbox has no id shared by two orgs). v2's own upsert targets `(id, organization_id)`, which the new unique index serves.
- Applied in sandbox on 2026-10-19, with the engineer's go:
  - `etl pg-migrate` on `public`: `etl_runs` gained the ledger columns, `etl_steps` and `alembic_version_etl` (head `0004`) were created, the unique index and the two `raw_meta_ad_creative` columns were added. The primary key and every row count are unchanged.
  - `etl ch-migrate` on `clevrr_db`: `ch_migrations` created; `0001` changed nothing (the tables exist); `0002` / `0003` recreated `order_mv` and `order_click_attribution` replicated and sharded by the order. The first copy moved no rows (the renamed-Distributed problem above), so the rows were copied by hand from the `*_shard_legacy` tables and the migration files were corrected. After the copy both replicas of each shard agree, `order_mv` holds every order from both replicas of the old unreplicated table (legacy's reads saw one replica's part only), and `order_click_attribution` has one row per order (the `rand()` duplicates are gone). Still present, for the engineer to drop: the empty Distributed shells `order_mv_legacy` and `order_click_attribution_legacy`, and the two `*_shard_legacy` tables.
  - `etl-config`: `ETL_V2_CUTOVER=true`; `POSTGRES__TARGET_SCHEMA` and `CLICKHOUSE__DATABASE` removed; `CLICKHOUSE__CLUSTER` and `RUN__ORG_ALLOWLIST` kept. The CronJobs stay suspended.
  - `kubectl create job --from=cronjob/etl-products-cost` succeeded against `public` / `clevrr_db`: the costs mirror replaced the org's rows (legacy appended the same rows to a non-Replacing table nightly, so the ClickHouse count fell to the Postgres count), and the run and its steps are in `public.etl_runs` / `public.etl_steps`.
  - Found: the live `etl-products-cost` CronJob carries `ETL_ORG_IDS` with one org (its file says the variable is omitted on purpose), so it mirrors that org only, as it did on the legacy image.
  - CI had already moved the seven CronJobs to the `clevrr-etl-v2:<git sha>` image from the engineer's push to `Staging`; the costs run above used that image, so the CI deploy path is proven end to end. The Delhivery and Amazon CronJobs stay on the legacy image; every ETL CronJob is still suspended.
- Open: drop the four `*_legacy` ClickHouse tables when the engineer is satisfied; decide on dropping `etl_v2` / `clevrr_db_v2`; `terraform apply` for the tfvars change, or delete the redundant Cloud Run pipeline jobs first.

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
