# ETL v2 on Google Cloud (plan 7.5): Cloud Run Jobs, a Cloud Workflow for the nightly chain,
# Cloud Scheduler, service accounts. State in GCS (partial config: see backend.hcl.example).
terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
  backend "gcs" {}
}

provider "google" {
  project = var.project
  region  = var.region
}
