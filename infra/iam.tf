# Service accounts, least privilege:
# - etl:       the jobs' identity; reads only its own secrets (no project-wide roles).
# - workflow:  runs the jobs and waits for their executions.
# - scheduler: starts the nightly workflow and the sweep job.

resource "google_project_service" "api" {
  for_each = toset([
    "run.googleapis.com", "workflows.googleapis.com", "workflowexecutions.googleapis.com",
    "cloudscheduler.googleapis.com", "secretmanager.googleapis.com", "vpcaccess.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

resource "google_service_account" "etl" {
  account_id   = "clevrr-etl-v2"
  display_name = "Clevrr ETL v2 jobs"
}

resource "google_service_account" "workflow" {
  account_id   = "clevrr-etl-v2-workflow"
  display_name = "Clevrr ETL v2 nightly workflow"
}

resource "google_service_account" "scheduler" {
  account_id   = "clevrr-etl-v2-scheduler"
  display_name = "Clevrr ETL v2 scheduler"
}

resource "google_secret_manager_secret_iam_member" "etl" {
  for_each  = toset(values(local.secret_env))
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.etl.email}"
}

# Run any ETL job (run.jobs.run) and read its executions to wait for them.
resource "google_cloud_run_v2_job_iam_member" "workflow_runs" {
  for_each = google_cloud_run_v2_job.job
  name     = each.value.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.workflow.email}"
}

resource "google_project_iam_member" "workflow_viewer" {
  for_each = toset(["roles/run.viewer", "roles/logging.logWriter"])
  project  = var.project
  role     = each.value
  member   = "serviceAccount:${google_service_account.workflow.email}"
}

resource "google_project_iam_member" "scheduler_workflows" {
  project = var.project
  role    = "roles/workflows.invoker"
  member  = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_cloud_run_v2_job_iam_member" "scheduler_sweep" {
  name     = google_cloud_run_v2_job.job["etl-sweep"].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}
