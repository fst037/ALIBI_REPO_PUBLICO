# SageMaker Training Jobs are not Terraform-managed resources (no aws_sagemaker_training_job
# resource exists in the provider). This module generates two files in the working directory:
#
#   training_job_<run_id>.json  — job config for aws sagemaker create-training-job
#   launch_training_<run_id>.ps1 — PowerShell script that submits the job
#
# To launch a training job:
#   1. terraform apply -target module.training
#   2. Run the generated .ps1 from your terminal (Zscaler: --no-verify-ssl is included)
#
# To re-run with different hyperparameters:
#   1. Change training_run_id in terraform.tfvars
#   2. terraform apply -target module.training  (generates new files)
#   3. Run the new launch_training_<run_id>.ps1

locals {
  job_name      = replace("${var.model_name}-${var.environment}-${var.run_id}", "_", "-")
  code_s3       = "s3://${var.artifacts_bucket}/code/${var.code_name}.tar.gz"
  output_s3     = "s3://${var.artifacts_bucket}/${var.model_name}/runs/${var.run_id}/"
  checkpoint_s3 = "s3://${var.artifacts_bucket}/${var.model_name}/checkpoints/${var.run_id}/"
  artifact_s3   = "${local.output_s3}${local.job_name}/output/model.tar.gz"

  stopping_condition = merge(
    { MaxRuntimeInSeconds = var.max_runtime_seconds },
    var.use_spot ? { MaxWaitTimeInSeconds = var.max_runtime_seconds * 3 } : {},
  )

  # Always on, not only for spot: a 12 h on-demand run that dies at its runtime
  # cap (or on a transient failure) would otherwise lose every step. SageMaker
  # syncs /opt/ml/checkpoints to S3 as it is written, and restores it into a
  # resubmitted job with the same run id, where train_qlora.py resumes from it.
  checkpoint_block = {
    CheckpointConfig = {
      S3Uri     = local.checkpoint_s3
      LocalPath = "/opt/ml/checkpoints"
    }
  }

  job_config = merge(
    {
      TrainingJobName = local.job_name
      RoleArn         = var.role_arn

      AlgorithmSpecification = {
        TrainingImage     = var.training_image
        TrainingInputMode = "File"

        # SageMaker scrapes the job's stdout with these regexes and publishes
        # each match as a CloudWatch metric, graphed live on the training job's
        # own console page. No agent, no server, no tracking backend, and no
        # cost beyond the log lines the job already writes — which is why the
        # MLflow server is optional rather than required for watching a run.
        #
        # `'?` after the colon: transformers 5 prints values as quoted strings
        # ({'loss': '2.969'}), 4.x printed bare numbers. Both must match, or the
        # job completes with an empty metric graph (found on smoke-003).
        # The patterns match transformers' Trainer log dicts, printed every
        # `logging_steps`:
        #   {'loss': 1.8234, 'grad_norm': 0.52, 'learning_rate': 0.00019, 'epoch': 0.13}
        #   {'eval_loss': 1.7012, 'eval_runtime': 12.3, 'epoch': 0.5}
        #
        # "'loss'" cannot collide with "'eval_loss'" — the character before
        # `loss` is a quote in one and an underscore in the other.
        MetricDefinitions = [
          { Name = "train:loss", Regex = "'loss': '?([0-9\\.]+)" },
          { Name = "eval:loss", Regex = "'eval_loss': '?([0-9\\.]+)" },
          { Name = "train:learning_rate", Regex = "'learning_rate': '?([0-9\\.e\\-\\+]+)" },
          { Name = "train:grad_norm", Regex = "'grad_norm': '?([0-9\\.]+)" },
          { Name = "train:epoch", Regex = "'epoch': '?([0-9\\.]+)" },
          { Name = "train:token_accuracy", Regex = "'mean_token_accuracy': '?([0-9\\.]+)" },
        ]
      }

      HyperParameters = merge(
        {
          sagemaker_program          = var.entry_point
          sagemaker_submit_directory = local.code_s3
        },
        var.hyperparameters,
      )

      InputDataConfig = [
        {
          ChannelName = "train"
          DataSource = {
            S3DataSource = {
              S3DataType = "S3Prefix"
              S3Uri      = "s3://${var.datasets_bucket}/${var.model_name}/train/"
            }
          }
          ContentType = "application/jsonlines"
        },
        {
          ChannelName = "eval"
          DataSource = {
            S3DataSource = {
              S3DataType = "S3Prefix"
              S3Uri      = "s3://${var.datasets_bucket}/${var.model_name}/eval/"
            }
          }
          ContentType = "application/jsonlines"
        },
      ]

      OutputDataConfig = { S3OutputPath = local.output_s3 }

      ResourceConfig = {
        InstanceType    = var.instance_type
        InstanceCount   = 1
        VolumeSizeInGB  = var.volume_size_gb
      }

      StoppingCondition            = local.stopping_condition
      EnableManagedSpotTraining    = var.use_spot

      Tags = [
        { Key = "Project",     Value = "alibi" },
        { Key = "Environment", Value = var.environment },
        { Key = "Module",      Value = var.model_name },
        { Key = "RunId",       Value = var.run_id },
      ]
    },
    local.checkpoint_block,
  )
}

# Job config JSON — consumed by the launch script via --cli-input-json
resource "local_file" "job_config" {
  filename        = "${path.root}/training_job_${var.run_id}.json"
  content         = jsonencode(local.job_config)
  file_permission = "0644"
}

# PowerShell launch script — run this from your terminal to submit the training job
resource "local_file" "launch_script" {
  filename        = "${path.root}/launch_training_${var.run_id}.ps1"
  file_permission = "0755"
  content         = <<-EOT
    # Auto-generated by Terraform — do not edit manually.
    # Run from your terminal to submit the SageMaker training job.
    # Zscaler proxy: --no-verify-ssl is included.

    # PRE-REQUISITE: package and upload training code first (README §3):
    #   cd d:\repos\ALIBI\ml\src
    #   tar -czf C:\Temp\model_a.tar.gz model_a\ common\ requirements.txt
    #   aws s3 cp C:\Temp\model_a.tar.gz s3://${var.artifacts_bucket}/code/${var.code_name}.tar.gz --profile alibi --no-verify-ssl

    $ErrorActionPreference = "Stop"
    $JobName = "${local.job_name}"
    $ConfigFile = Join-Path $PSScriptRoot "training_job_${var.run_id}.json"

    Write-Host "[launch] submitting job: $JobName"
    aws sagemaker create-training-job `
      --cli-input-json "file://$ConfigFile" `
      --profile alibi `
      --no-verify-ssl

    Write-Host ""
    Write-Host "[launch] submitted. Monitor with:"
    Write-Host "  aws sagemaker describe-training-job --training-job-name $JobName --profile alibi --no-verify-ssl --query TrainingJobStatus"
    Write-Host "  aws sagemaker wait training-job-completed-or-stopped --training-job-name $JobName --profile alibi --no-verify-ssl"
    Write-Host ""
    Write-Host "[launch] artifact will land at:"
    Write-Host "  ${local.artifact_s3}"
  EOT
}
