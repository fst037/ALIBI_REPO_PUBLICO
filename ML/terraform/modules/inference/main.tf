# SageMaker real-time inference: ONE endpoint, ONE inference component carrying
# the base model and both LoRA adapters (sql = Module A, chart = Module C).
#
# Why an inference component (IC) instead of a model on the variant: SageMaker
# can scale a real-time endpoint to ZERO instances only when the endpoint hosts
# inference components (docs: "Scale an endpoint to zero instances"). The
# previous version of this module put Application Auto Scaling min_capacity = 0
# on `sagemaker:variant:DesiredInstanceCount` of a classic endpoint — which the
# service does not support for real-time endpoints, so it never scaled to zero.
#
# How scale-to-zero works here (enable_scale_to_zero = true):
#   - The endpoint config's ManagedInstanceScaling has MinInstanceCount = 0, so
#     SageMaker releases the instance once no IC copy needs it.
#   - A target-tracking policy on the IC's copy count scales IN to 0 copies after
#     about 15 minutes with no invocations (its low alarm's evaluation window).
#   - Scaling OUT from 0: the backend asks for a copy itself the moment a run
#     needs the model (UpdateInferenceComponentRuntimeConfig, app/core/sagemaker.py
#     `ensure_awake`), and shows the wait. The step-scaling policy + alarm on
#     NoCapacityInvocationFailures below is the safety net for any other caller
#     (the deploy workflow's test question, a CLI invoke): a refused invocation
#     wakes it within ~2 minutes.
#   - Coming back takes several minutes: an instance, the image, then the
#     handler loading the base. Without the quantised-base cache that is a ~55 GB
#     HuggingFace download plus on-GPU quantisation (~10 min measured 2026-10-15);
#     with `quant_cache_s3_uri` set it is a ~17 GB parallel S3 download.
#
# enable_scale_to_zero = false keeps the same shape at one copy, always on.
#
# Deploy:   ml-deploy.yml endpoint_action=deploy (packages adapters + handler)
# Destroy:  ml-destroy.yml destroy_inference

locals {
  name_prefix = replace("${var.model_name}-${var.environment}", "_", "-")
  # The SageMaker model carries the deployment id so a different run can be
  # swapped in (create-before-destroy); the endpoint, its config and the IC keep
  # stable names, so clients — the backend's ALIBI_SAGEMAKER_* settings — never
  # change when the model does.
  deploy_base = var.deployment_id == "" ? local.name_prefix : "${local.name_prefix}-${replace(var.deployment_id, "_", "-")}"
  # A SageMaker model cannot be updated, and create-before-destroy needs the new
  # one to have a different name: changing only its environment (the quantised-base
  # cache) used to fail with "Cannot create already existing model". The suffix
  # makes turning the cache on a distinct model.
  deploy_name = var.quant_cache_s3_uri == "" ? local.deploy_base : "${local.deploy_base}-qc"
  ic_name     = "${local.name_prefix}-models"
}

resource "aws_sagemaker_model" "this" {
  name               = local.deploy_name
  execution_role_arn = var.role_arn

  primary_container {
    image          = var.inference_image
    model_data_url = var.model_artifact_s3_uri

    environment = merge(
      {
        SAGEMAKER_PROGRAM          = "inference.py"
        SAGEMAKER_SUBMIT_DIRECTORY = "/opt/ml/model/code"
        MODEL_TYPE                 = var.model_type
      },
      var.quant_cache_s3_uri == "" ? {} : { QUANT_CACHE_S3_URI = var.quant_cache_s3_uri },
    )
  }

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Project     = "alibi"
    Environment = var.environment
    Module      = var.model_name
    ModelType   = var.model_type
  }
}

# The instance is chosen from an ordered list (InstancePools), not fixed: on
# 2026-10-13 and 2026-10-20 a single type sat for 15-40 minutes with no
# capacity, and the only configuration that got a GPU was this list, created by
# hand. SageMaker tries priority 1 first and
# falls back down the list when that type has no capacity.
#
# awscc, not aws: the AWS provider's endpoint configuration has no
# instance_pools (hashicorp/terraform-provider-aws#48469); CloudFormation's
# AWS::SageMaker::EndpointConfig does.
#
# Changing the list is a destroy + deploy, not an apply: SageMaker refuses to
# move an endpoint that hosts inference components to a config with other
# instance types ("Cannot update endpoint with a different instance type").
resource "awscc_sagemaker_endpoint_config" "this" {
  # The list is in the name (hashed): a different list is a new config, and the
  # replacement is create-before-destroy, so a fixed name would collide with the
  # config it replaces ("Cannot create already existing endpoint configuration",
  # 2026-10-13).
  endpoint_config_name = "${local.name_prefix}-ic-${substr(sha1(join(",", var.instance_types)), 0, 8)}"
  # Required when the variant names no model (the IC does): the role SageMaker
  # uses to place IC copies on the instances it manages.
  execution_role_arn = var.role_arn

  production_variants = [{
    variant_name           = "primary"
    initial_instance_count = 1
    instance_pools = [
      for i, type in var.instance_types : { instance_type = type, priority = i + 1 }
    ]

    managed_instance_scaling = {
      status             = "ENABLED"
      min_instance_count = var.enable_scale_to_zero ? 0 : 1
      max_instance_count = 1
    }

    routing_config = {
      routing_strategy = "LEAST_OUTSTANDING_REQUESTS"
    }
  }]

  tags = [
    { key = "Project", value = "alibi" },
    { key = "Environment", value = var.environment },
  ]

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_sagemaker_endpoint" "this" {
  name                 = local.name_prefix
  endpoint_config_name = awscc_sagemaker_endpoint_config.this.endpoint_config_name

  tags = {
    Project     = "alibi"
    Environment = var.environment
    Module      = var.model_name
  }
}

# The AWS provider has no inference-component resource; the Cloud Control one
# (awscc, AWS::SageMaker::InferenceComponent) does.
resource "awscc_sagemaker_inference_component" "this" {
  inference_component_name = local.ic_name
  endpoint_name            = aws_sagemaker_endpoint.this.name
  variant_name             = "primary"

  specification = {
    model_name = aws_sagemaker_model.this.name
    compute_resource_requirements = {
      # The whole GPU: one base model, both adapters, switched per request.
      number_of_accelerator_devices_required = 1
      min_memory_required_in_mb              = var.min_memory_mb
    }
    startup_parameters = {
      # The handler downloads the base from HuggingFace and loads it 4-bit
      # before it can answer; the defaults are far too short for a 27B.
      model_data_download_timeout_in_seconds            = var.startup_timeout_seconds
      container_startup_health_check_timeout_in_seconds = var.startup_timeout_seconds
    }
  }

  runtime_config = {
    copy_count = 1
  }

  tags = [
    { key = "Project", value = "alibi" },
    { key = "Environment", value = var.environment },
  ]

  lifecycle {
    # Auto scaling (and the backend's wake) own the copy count once it exists;
    # re-applying must not snap it back to 1 or fight a scale-in.
    ignore_changes = [runtime_config]
  }
}

# ── Scale to zero (Application Auto Scaling on the IC's copy count) ───────────

resource "aws_appautoscaling_target" "copies" {
  count              = var.enable_scale_to_zero ? 1 : 0
  service_namespace  = "sagemaker"
  resource_id        = "inference-component/${awscc_sagemaker_inference_component.this.inference_component_name}"
  scalable_dimension = "sagemaker:inference-component:DesiredCopyCount"
  min_capacity       = 0
  max_capacity       = 1
}

# Scale-in to zero when idle. Scale-out above 1 is capped by max_capacity.
resource "aws_appautoscaling_policy" "idle_to_zero" {
  count              = var.enable_scale_to_zero ? 1 : 0
  name               = "${local.name_prefix}-idle-to-zero"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.copies[0].service_namespace
  resource_id        = aws_appautoscaling_target.copies[0].resource_id
  scalable_dimension = aws_appautoscaling_target.copies[0].scalable_dimension

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "SageMakerInferenceComponentInvocationsPerCopy"
    }
    target_value       = 1.0
    scale_in_cooldown  = var.scale_in_cooldown_seconds
    scale_out_cooldown = 60
  }
}

# Scale-out from zero for callers that do not wake it themselves.
resource "aws_appautoscaling_policy" "wake_on_refusal" {
  count              = var.enable_scale_to_zero ? 1 : 0
  name               = "${local.name_prefix}-wake-on-refusal"
  policy_type        = "StepScaling"
  service_namespace  = aws_appautoscaling_target.copies[0].service_namespace
  resource_id        = aws_appautoscaling_target.copies[0].resource_id
  scalable_dimension = aws_appautoscaling_target.copies[0].scalable_dimension

  step_scaling_policy_configuration {
    adjustment_type         = "ChangeInCapacity"
    metric_aggregation_type = "Maximum"
    cooldown                = 60

    step_adjustment {
      metric_interval_lower_bound = 0
      scaling_adjustment          = 1
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "refused_while_asleep" {
  count               = var.enable_scale_to_zero ? 1 : 0
  alarm_name          = "${local.name_prefix}-refused-while-asleep"
  alarm_description   = "An invocation found the models' inference component at zero copies; wake it."
  namespace           = "AWS/SageMaker"
  metric_name         = "NoCapacityInvocationFailures"
  dimensions          = { InferenceComponentName = awscc_sagemaker_inference_component.this.inference_component_name }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_appautoscaling_policy.wake_on_refusal[0].arn]

  tags = {
    Project     = "alibi"
    Environment = var.environment
  }
}
