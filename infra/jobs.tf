# Two Cloud Run Jobs, for the migrations only (plan 7.5, reduced 2026-10-21): `etl pg-migrate`
# and `etl ch-migrate` run the same image and reach the databases over the VPC connector, so a
# schema change needs no tunnel. The pipelines run in the existing Kubernetes CronJobs
# (cronjobs_dev/); their Cloud Run Jobs, the nightly Workflow and the schedulers were removed
# on 2026-10-21 (the engineer's decision).

locals {
  jobs = {
    "etl-pg-migrate" = { args = ["pg-migrate"], memory = "1Gi", cpu = "1", timeout = "900s" }
    "etl-ch-migrate" = { args = ["ch-migrate"], memory = "1Gi", cpu = "1", timeout = "900s" }
  }
  plain_env = {
    ENV                     = "sbx"
    POSTGRES__HOST          = var.postgres.host
    POSTGRES__PORT          = tostring(var.postgres.port)
    POSTGRES__DB            = var.postgres.db
    POSTGRES__USER          = var.postgres.user
    POSTGRES__TARGET_SCHEMA = var.postgres.target_schema
    CLICKHOUSE__HOST        = var.clickhouse.host
    CLICKHOUSE__PORT        = tostring(var.clickhouse.port)
    CLICKHOUSE__USER        = var.clickhouse.user
    CLICKHOUSE__DATABASE    = var.clickhouse.database
    CLICKHOUSE__CLUSTER     = var.clickhouse.cluster
    RUN__ORG_ALLOWLIST      = jsonencode(var.org_allowlist)
  }
  secret_env = merge({
    POSTGRES__PASSWORD   = var.postgres.password_secret
    CLICKHOUSE__PASSWORD = var.clickhouse.password_secret
  }, var.optional_secrets)
}

resource "google_cloud_run_v2_job" "job" {
  for_each            = local.jobs
  name                = each.key
  location            = var.region
  deletion_protection = false
  labels              = { app = "clevrr-etl-v2" } # CI finds the jobs by this label

  template {
    task_count = 1
    template {
      service_account = google_service_account.etl.email
      max_retries     = 1
      timeout         = each.value.timeout
      vpc_access {
        connector = var.vpc_connector
        egress    = "PRIVATE_RANGES_ONLY"
      }
      containers {
        image = "${var.image}:${var.image_tag}"
        args  = each.value.args
        resources {
          limits = { memory = each.value.memory, cpu = each.value.cpu }
        }
        dynamic "env" {
          for_each = local.plain_env
          content {
            name  = env.key
            value = env.value
          }
        }
        dynamic "env" {
          for_each = local.secret_env
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = env.value
                version = "latest"
              }
            }
          }
        }
      }
    }
  }

  lifecycle {
    # CI moves the image tag (gcloud run jobs update --image); terraform keeps the rest.
    ignore_changes = [template[0].template[0].containers[0].image]
  }
  depends_on = [google_secret_manager_secret_iam_member.etl, google_project_service.api]
}
