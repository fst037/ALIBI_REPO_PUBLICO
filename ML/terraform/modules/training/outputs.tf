output "training_job_name" {
  description = "Name of the training job that will be submitted. Run the generated .ps1 to submit it."
  value       = local.job_name
}

output "artifact_s3_uri" {
  description = "S3 URI where the trained model artifact will land after the job completes. Pass to inference module."
  value       = local.artifact_s3
}

output "launch_script_path" {
  description = "Path to the generated PowerShell script. Run it from your terminal to submit the training job."
  value       = local_file.launch_script.filename
}

output "job_config_path" {
  description = "Path to the generated job config JSON consumed by the launch script."
  value       = local_file.job_config.filename
}
