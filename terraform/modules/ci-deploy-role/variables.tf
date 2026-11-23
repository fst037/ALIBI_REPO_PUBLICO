variable "github_org" {
  type    = string
  default = "Martina2612"
}

variable "github_repo" {
  type    = string
  default = "ALIBI"
}

variable "allowed_branches" {
  description = "Branch patterns allowed to assume the deploy role via OIDC (GitHub ref StringLike pattern, e.g. \"main\" or \"*\"). Every workflow that uses this role (infra-deploy.yml, infra-destroy.yml, backend-deploy.yml, frontend-deploy.yml, db-deploy.yml, ml-deploy.yml, ml-train.yml, ml-destroy.yml) is either workflow_dispatch-only or push-triggered-and-scoped-to-main/dev already at the GitHub Actions trigger level — a manual click or a push to main/dev already gates who can run them, so restricting the OIDC trust policy by branch on top of that adds friction without much real security benefit for a single-tenant thesis account."
  type        = list(string)
  default     = ["*"]
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "state_bucket_arn" {
  description = "ARN of the shared Terraform state bucket (alibi-terraform-state-...)"
  type        = string
}

variable "state_key_prefix" {
  description = "State key prefix this role may read/write (alibi/unified/<environment>/*)"
  type        = string
}
