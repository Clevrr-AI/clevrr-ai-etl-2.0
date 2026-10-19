# One Cloud Run Job per pipeline (plan 7.5). Every job runs the same image with its own args;
# `etl run` shards the orgs over the job's tasks. Retries: 1. Timeouts and memory per pipeline.

locals {
  jobs = {
    "etl-pg-migrate"          = { args = ["pg-migrate"], memory = "1Gi", cpu = "1", timeout = "900s" }
    "etl-ch-migrate"          = { args = ["ch-migrate"], memory = "1Gi", cpu = "1", timeout = "900s" }
    "etl-sweep"               = { args = ["sweep"], memory = "512Mi", cpu = "1", timeout = "300s" }
    "etl-shopify-catalog"     = { args = ["run", "shopify-catalog"], memory = "2Gi", cpu = "1", timeout = "3600s" }
    "etl-shopify-sales"       = { args = ["run", "shopify-sales"], memory = "4Gi", cpu = "2", timeout = "7200s" }
    "etl-meta-ads"            = { args = ["run", "meta-ads"], memory = "2Gi", cpu = "1", timeout = "7200s" }
    "etl-meta-ads-region"     = { args = ["run", "meta-ads-region"], memory = "1Gi", cpu = "1", timeout = "3600s" }
    "etl-google-ads"          = { args = ["run", "google-ads"], memory = "2Gi", cpu = "1", timeout = "7200s" }
    "etl-costs"               = { args = ["run", "costs"], memory = "1Gi", cpu = "1", timeout = "1800s" }
    "etl-reliability-shopify" = { args = ["run", "reliability-shopify"], memory = "1Gi", cpu = "1", timeout = "1800s" }
    "etl-reliability-meta"    = { args = ["run", "reliability-meta"], memory = "1Gi", cpu = "1", timeout = "1800s" }
    "etl-reliability-google"  = { args = ["run", "reliability-google"], memory = "1Gi", cpu = "1", timeout = "1800s" }
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
    task_count = contains(["etl-pg-migrate", "etl-ch-migrate", "etl-sweep"], each.key) ? 1 : var.task_count
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
