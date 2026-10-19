output "jobs" {
  value = sort([for j in google_cloud_run_v2_job.job : j.name])
}

output "workflow" {
  value = google_workflows_workflow.nightly.id
}

output "ci_workload_identity_provider" {
  value = local.ci == 1 ? google_iam_workload_identity_pool_provider.github[0].name : null
}

output "ci_service_account" {
  value = local.ci == 1 ? google_service_account.ci[0].email : null
}
