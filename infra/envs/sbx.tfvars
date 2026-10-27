# Sandbox (clevrr-ai-sbx): writes the sandbox's own tables (public / clevrr_db, the TL's decision 2026-10-19), pilot orgs only,
# the two migrate jobs only since 2026-10-21 (the pipelines run in the Kubernetes CronJobs). Values that are not secret;
# passwords come from Secret Manager by name.
project       = "clevrr-ai-sbx"
region        = "asia-south1"
image         = "asia-south1-docker.pkg.dev/clevrr-ai-sbx/clevrr-registry-sbx/clevrr-etl-v2"
vpc_connector = "projects/clevrr-ai-sbx/locations/asia-south1/connectors/clevrr-vpc-sbx-sql"

postgres = {
  host            = "10.80.112.3"
  port            = 5432
  db              = "clevrr_db"
  user            = "postgres"
  target_schema   = "public"
  password_secret = "v2-postgres-password"
}

clickhouse = {
  host            = "10.160.0.8"
  port            = 8123
  user            = "app"
  database        = "clevrr_db"
  cluster         = "sbx-cluster"
  password_secret = "v2-clickhouse-password"
}

org_allowlist     = ["legendoftoys.com", "shapercult.com", "mydesignation.com"]
optional_secrets  = {}
github_repository = "Clevrr-AI/clevrr-ai-etl-2.0"
deploy_branch     = "Staging"
gke               = { name = "clevrr-ckh-cluster-sandbox", zone = "asia-south1-a" }
