# Service accounts, least privilege:
# - etl: the jobs' identity; reads only its own secrets (no project-wide roles).
# The workflow and scheduler accounts went with the nightly Workflow (2026-10-21).

resource "google_project_service" "api" {
  for_each = toset([
    "run.googleapis.com", "secretmanager.googleapis.com", "vpcaccess.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

resource "google_service_account" "etl" {
  account_id   = "clevrr-etl-v2"
  display_name = "Clevrr ETL v2 jobs"
}

resource "google_secret_manager_secret_iam_member" "etl" {
  for_each  = toset(values(local.secret_env))
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.etl.email}"
}
