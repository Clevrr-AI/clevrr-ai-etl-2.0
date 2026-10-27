# infra (plan 7.5, reduced)

Terraform for the Google Cloud side of ETL v2. Since 2026-10-21 it holds two Cloud Run Jobs,
`etl-pg-migrate` and `etl-ch-migrate` (the same image, reaching the databases over the VPC
connector, so a schema change needs no tunnel), the jobs' service account with access to its
own secrets only, Workload Identity Federation for CI (`ci.tf`) and CI's read access to the
GKE cluster (`k8s.tf`). The pipelines run in the existing Kubernetes CronJobs (`cronjobs_dev/`,
`cronjobs/`); the one-job-per-pipeline Cloud Run Jobs, the `etl-v2-nightly` Workflow and the
schedulers were removed on 2026-10-21 (the engineer's decision).

## Use (sandbox only)

1. `cp backend.hcl.example backend.hcl` and create the state bucket once (see the file).
2. `terraform init -backend-config=backend.hcl`
3. `terraform plan -var-file=envs/sbx.tfvars -var image_tag=<git sha>` and read the plan.
4. `terraform apply ...` only with the engineer's go.

Run a migration: `gcloud run jobs execute etl-pg-migrate --region=asia-south1 --project=clevrr-ai-sbx --wait`
(the same for `etl-ch-migrate`). CI moves both jobs to each new image (`.github/workflows/ci.yaml`).

Rules: never the prod project; no secret values in tfvars (secrets by name only); the image tag
is the git SHA, never `latest`. Terraform here authenticates with
`GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token)` when application-default
credentials are not set up.
