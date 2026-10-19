# The ETL runs in the existing GKE cluster as the existing `etl-all-*` CronJobs (cronjobs_dev/,
# cronjobs/), with their own ConfigMap, Secret and `cloud-sql-proxy` service account; none of
# that is managed here. This file holds only what CI needs on the GCP side: read the
# cluster's credentials. The RoleBinding in cronjobs_dev/_ci-rbac.yaml then allows only
# `kubectl set image` on the CronJobs.

variable "gke" {
  description = "The GKE cluster the CronJobs run in (name, zone); its namespace is `etl`."
  type = object({
    name = string
    zone = string
  })
}

resource "google_project_iam_member" "ci_cluster_viewer" {
  count   = local.ci
  project = var.project
  role    = "roles/container.clusterViewer"
  member  = "serviceAccount:${google_service_account.ci[0].email}"
}

output "gke_cluster" {
  value = var.gke
}
