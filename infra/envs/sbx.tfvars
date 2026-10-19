# Sandbox (clevrr-ai-sbx) during parity: writes etl_v2 / clevrr_db_v2 only, pilot orgs only,
# no schedule until parity is signed off (run the workflow by hand). Values that are not secret;
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
  target_schema   = "etl_v2"
  password_secret = "v2-postgres-password"
}

clickhouse = {
  host            = "10.160.0.8"
  port            = 8123
  user            = "app"
  database        = "clevrr_db_v2"
  cluster         = "sbx-cluster"
  password_secret = "v2-clickhouse-password"
}

org_allowlist     = ["legendoftoys.com", "shapercult.com", "mydesignation.com"]
optional_secrets  = {}
task_count        = 1
nightly_schedule  = ""
github_repository = "Clevrr-AI/clevrr-ai-etl-2.0"
deploy_branch     = "Staging"
gke               = { name = "clevrr-ckh-cluster-sandbox", zone = "asia-south1-a" }
