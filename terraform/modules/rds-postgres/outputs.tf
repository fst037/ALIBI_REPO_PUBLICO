output "instance_arn" {
  value = aws_db_instance.this.arn
}

output "instance_identifier" {
  value = aws_db_instance.this.identifier
}

output "secret_arn" {
  description = "ARN of the RDS-managed master user secret in Secrets Manager (rds!db-* naming). Null if manage_master_user_password isn't actually enabled on the real instance."
  value       = try(aws_db_instance.this.master_user_secret[0].secret_arn, null)
}

output "database_name" {
  value = aws_db_instance.this.db_name
}

output "endpoint" {
  description = "host:port for a direct Postgres connection once a client (e.g. the backend Lambda, once VPC-attached) can reach this VPC."
  value       = aws_db_instance.this.endpoint
}
