# infra (plan 7.5)

Terraform for ETL v2 on Google Cloud: one Cloud Run Job per pipeline (plus `etl-pg-migrate`,
`etl-ch-migrate`, `etl-sweep`), the `etl-v2-nightly` Cloud Workflow (migrate → catalog → sales →
Meta ‖ Meta region ‖ Google → costs → reliability, fail fast), Cloud Scheduler (nightly + sweep
every 30 min, only when `nightly_schedule` is set), three service accounts with least privilege,
and optional Workload Identity Federation for CI.

## Use (sandbox only during parity)

1. `cp backend.hcl.example backend.hcl` and create the state bucket once (see the file).
2. `terraform init -backend-config=backend.hcl`
3. `terraform plan -var-file=envs/sbx.tfvars -var image_tag=<git sha>` and read the plan.
4. `terraform apply ...` only with the engineer's go.

Run the chain by hand: `gcloud workflows run etl-v2-nightly --location=asia-south1 --project=clevrr-ai-sbx`.
A backfill: `gcloud run jobs execute etl-shopify-sales --region=asia-south1 --project=clevrr-ai-sbx
--args=run,shopify-sales,--org,shapercult.com,--from,2026-09-01,--to,2026-10-01`.

Rules: never the prod project; no secret values in tfvars (secrets by name only); the image tag
is the git SHA, never `latest`.

First apply in a project: if the workflow fails with "Workflows service agent does not exist",
run `gcloud beta services identity create --service=workflows.googleapis.com --project=<project>`
and apply again. Terraform here authenticates with `GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token)`
when application-default credentials are not set up.
