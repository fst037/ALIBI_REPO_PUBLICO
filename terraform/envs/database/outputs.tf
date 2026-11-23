# Consumed by .github/workflows/db-deploy.yml to auto-populate repo
# variables after apply, same pattern as terraform/envs/dev/outputs.tf.

output "db_instance_arn" {
  value = module.database.instance_arn
}

output "db_secret_arn" {
  value = module.database.secret_arn
}

output "db_name" {
  value = module.database.database_name
}

output "db_endpoint" {
  value = module.database.endpoint
}

output "bastion_instance_id" {
  description = "EC2 instance id of the SSM bastion — the --target for `aws ssm start-session` port forwarding to the DB."
  value       = module.bastion.instance_id
}

output "db_power_function_name" {
  description = "Invoke with an empty payload to start a stopped database (idempotent)."
  value       = module.autostop.function_name
}
