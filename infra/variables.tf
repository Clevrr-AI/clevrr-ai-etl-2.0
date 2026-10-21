variable "project" {
  description = "GCP project (sandbox: clevrr-ai-sbx). Never the prod project during parity."
  type        = string
}

variable "region" {
  type    = string
  default = "asia-south1"
}

variable "image" {
  description = "Image repository without tag, e.g. asia-south1-docker.pkg.dev/<project>/<repo>/clevrr-etl-v2."
  type        = string
}

variable "image_tag" {
  description = "Image tag (the git SHA). CI moves the jobs to new tags; terraform ignores later tag changes."
  type        = string
}

variable "vpc_connector" {
  description = "Serverless VPC Access connector that reaches Cloud SQL and the ClickHouse ILB."
  type        = string
}

variable "postgres" {
  type = object({
    host            = string
    port            = number
    db              = string
    user            = string
    target_schema   = string
    password_secret = string
  })
}

variable "clickhouse" {
  type = object({
    host            = string
    port            = number
    user            = string
    database        = string
    cluster         = string
    password_secret = string
  })
}

variable "org_allowlist" {
  description = "Orgs the pipelines may run for (parity: the pilot orgs). Empty: every enabled org."
  type        = list(string)
  default     = []
}

variable "optional_secrets" {
  description = "Env var name -> Secret Manager secret, added only when set (Slack webhook, Google Ads OAuth client)."
  type        = map(string)
  default     = {}
}

variable "deploy_branch" {
  description = "The only branch whose pushes may deploy (GitHub OIDC ref condition)."
  type        = string
  default     = "Staging"
}

variable "github_repository" {
  description = "owner/repo allowed to deploy through Workload Identity Federation. Empty: no CI access."
  type        = string
  default     = ""
}
