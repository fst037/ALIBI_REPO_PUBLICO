output "instance_id" {
  description = "EC2 instance id of the bastion — the --target for `aws ssm start-session`."
  value       = aws_instance.bastion.id
}

output "security_group_id" {
  value = aws_security_group.bastion.id
}

output "role_arn" {
  description = "ARN of the bastion's instance role (SSM core only)."
  value       = aws_iam_role.bastion.arn
}
