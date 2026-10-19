resource "google_workflows_workflow" "nightly" {
  name            = "etl-v2-nightly"
  region          = var.region
  service_account = google_service_account.workflow.id
  source_contents = templatefile("${path.module}/nightly.yaml.tftpl", {
    project = var.project
    region  = var.region
  })
  depends_on = [google_project_service.api, google_cloud_run_v2_job.job]
}

resource "google_cloud_scheduler_job" "nightly" {
  count     = var.nightly_schedule == "" ? 0 : 1
  name      = "etl-v2-nightly"
  region    = var.region
  schedule  = var.nightly_schedule
  time_zone = "Etc/UTC"
  http_target {
    http_method = "POST"
    uri         = "https://workflowexecutions.googleapis.com/v1/${google_workflows_workflow.nightly.id}/executions"
    body        = base64encode("{}")
    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }
}

# The sweeper marks runs whose heartbeat stopped (a killed task) as `killed`.
resource "google_cloud_scheduler_job" "sweep" {
  count     = var.nightly_schedule == "" ? 0 : 1
  name      = "etl-v2-sweep"
  region    = var.region
  schedule  = "*/30 * * * *"
  time_zone = "Etc/UTC"
  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/${google_cloud_run_v2_job.job["etl-sweep"].id}:run"
    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }
}
