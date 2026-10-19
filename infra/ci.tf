# GitHub Actions deploys without a key (plan 7.7): Workload Identity Federation for one
# repository. The CI identity may push images to the registry and move the jobs to a new tag;
# it cannot change anything else. Off when var.github_repository is empty.

locals {
  ci = var.github_repository == "" ? 0 : 1
}

resource "google_iam_workload_identity_pool" "github" {
  count                     = local.ci
  workload_identity_pool_id = "etl-v2-github"
  display_name              = "ETL v2 GitHub Actions"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  count                              = local.ci
  workload_identity_pool_id          = google_iam_workload_identity_pool.github[0].workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }
  attribute_condition = "assertion.repository == \"${var.github_repository}\" && assertion.ref == \"refs/heads/main\""
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "ci" {
  count        = local.ci
  account_id   = "clevrr-etl-v2-ci"
  display_name = "Clevrr ETL v2 CI (image push, job image update)"
}

resource "google_service_account_iam_member" "ci_wif" {
  count              = local.ci
  service_account_id = google_service_account.ci[0].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github[0].name}/attribute.repository/${var.github_repository}"
}

# Push to the existing registry repository named in var.image.
resource "google_project_iam_member" "ci_push" {
  count   = local.ci
  project = var.project
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${google_service_account.ci[0].email}"
}

# Update the jobs' image, which needs actAs on the jobs' service account.
resource "google_cloud_run_v2_job_iam_member" "ci_update" {
  for_each = local.ci == 1 ? google_cloud_run_v2_job.job : {}
  name     = each.value.name
  location = var.region
  role     = "roles/run.developer"
  member   = "serviceAccount:${google_service_account.ci[0].email}"
}

resource "google_service_account_iam_member" "ci_act_as_etl" {
  count              = local.ci
  service_account_id = google_service_account.etl.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.ci[0].email}"
}
