# ALIBI database — plain RDS PostgreSQL (free-tier eligible).
#
# Deliberately NOT composed into terraform/envs/dev (the unified
# backend+frontend+ML-core root): this database holds real data, so there
# is no benefit — and real risk — to folding it into a routine
# deploy-all/destroy-all cycle. Applied on its own via
# .github/workflows/db-deploy.yml, kept alive indefinitely.
#
# Uses the same CI OIDC role as the unified root (module.ci_deploy_role,
# defined in terraform/envs/dev/main.tf) — its IAM policy
# (terraform/modules/ci-deploy-role) was extended with RDS + this state
# key's S3 prefix rather than creating a second role for one more root.

module "database" {
  source = "../../modules/rds-postgres"

  environment    = var.environment
  aws_region     = var.aws_region
  engine_version = var.engine_version
}

# SSM bastion for developer access to the private RDS instance — no inbound
# rules, IAM-authenticated port forwarding, so both developers connect from
# any IP without a security-group allowlist to maintain. Lives in the same
# root/state/lifecycle as the database since it exists solely to reach it.
module "bastion" {
  source = "../../modules/bastion"

  environment = var.environment
}

# Stops the database after an idle hour and starts it on demand — see the
# module header. The function names are the unified root's (terraform/envs/dev)
# backend API and ingest committer Lambdas, by naming convention: the two roots
# share no state.
module "autostop" {
  source = "../../modules/rds-autostop"

  environment            = var.environment
  db_instance_identifier = module.database.instance_identifier
  db_instance_arn        = module.database.instance_arn
  bastion_instance_id    = module.bastion.instance_id
  activity_function_names = [
    "alibi-backend-${var.environment}",
    "alibi-ingest-committer-${var.environment}",
  ]
}
