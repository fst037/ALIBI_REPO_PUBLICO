# Plain RDS PostgreSQL (not Aurora) — chosen specifically to use the AWS
# RDS Free Tier (db.t4g.micro, single-AZ, 750 instance-hours/month, 20 GB
# storage, 12 months from account creation). Aurora — including Serverless
# v2 — is never free-tier eligible regardless of instance size. Single
# database for users/auth, uploaded-file metadata, dashboard/chart
# configs, chat history (JSONB), the per-tenant tables Module B ingests
# into, and pgvector for Module B's column embeddings.
#
# Unlike Aurora, standard RDS has no Data API — this instance is reached
# via a direct socket connection, which means whatever calls it (the
# backend Lambda) must join this VPC. That Lambda-side wiring
# (vpc_config + AWSLambdaVPCAccessExecutionRole + a Lambda security group
# allowed to reach db_sg on 5432) is not part of this module — it's
# deferred until a backend route actually needs to query the database,
# same as the RDS Data API wiring was deferred under the prior Aurora
# design.

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_db_subnet_group" "this" {
  name       = "alibi-db-${var.environment}"
  subnet_ids = data.aws_subnets.default.ids
  tags       = { Project = "alibi", Environment = var.environment }
}

# Ingress scoped to the default VPC's own CIDR (not the internet) rather
# than a specific Lambda security group, so every VPC-attached function (the
# API, the ingest committer, the run worker) reaches it with no RDS-side change.
resource "aws_security_group" "db" {
  name        = "alibi-db-${var.environment}"
  description = "RDS instance access - Postgres from within the default VPC only, no internet ingress."
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "Postgres from within the default VPC"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.default.cidr_block]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Project = "alibi", Environment = var.environment }
}

resource "aws_db_instance" "this" {
  identifier     = "alibi-${var.environment}"
  engine         = "postgres"
  engine_version = var.engine_version
  instance_class = var.instance_class

  allocated_storage = var.allocated_storage
  storage_type      = "gp3"
  storage_encrypted = true

  db_name  = var.database_name
  username = var.master_username

  # AWS creates and rotates the master password in Secrets Manager —
  # nothing sensitive lands in Terraform state or a repo secret.
  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false
  multi_az               = false # free-tier eligibility requires single-AZ

  deletion_protection       = var.deletion_protection
  skip_final_snapshot       = false
  final_snapshot_identifier = "alibi-${var.environment}-final"

  tags = { Project = "alibi", Environment = var.environment }

  lifecycle {
    # RDS refuses to "move" a DB instance to a subnet group AWS considers
    # in the same VPC as its current one (InvalidVPCNetworkStateFault),
    # even when — as here — the destination spans the exact same subnets.
    # aws_db_subnet_group.this still gets created for a future replacement
    # to use; this instance just keeps whatever subnet group it already has.
    ignore_changes = [db_subnet_group_name]
  }
}
