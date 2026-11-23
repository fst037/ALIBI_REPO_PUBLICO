variable "environment" {
  type = string
}

variable "aws_region" {
  type = string
}

# Plain RDS PostgreSQL, not Aurora — Aurora (any mode, including
# Serverless v2) is never covered by the AWS RDS Free Tier, only the
# standard engines are.
#
# Pinned to 16.14 to match the actual instance already running in AWS
# (created manually before this module existed, then imported into this
# module's state) — RDS doesn't support downgrading engine versions, so
# this must track whatever's really deployed, not an arbitrary default.
variable "engine_version" {
  type    = string
  default = "16.14"
}

# db.t4g.micro + single-AZ is the free-tier-eligible shape: 750
# instance-hours/month (enough for one instance running continuously)
# for 12 months from account creation.
variable "instance_class" {
  type    = string
  default = "db.t4g.micro"
}

# Free tier covers up to 20 GB of gp2/gp3 storage.
variable "allocated_storage" {
  type    = number
  default = 20
}

variable "master_username" {
  type    = string
  default = "alibi_admin"
}

variable "database_name" {
  type    = string
  default = "alibi"
}

# true by default: this database is applied manually and kept alive
# indefinitely (never part of the unified root's destroy-all cycle).
# Deletion protection guards against an
# accidental `terraform destroy` targeting this module.
variable "deletion_protection" {
  type    = bool
  default = true
}
