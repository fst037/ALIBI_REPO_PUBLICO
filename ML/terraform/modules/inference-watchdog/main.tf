# Backstop for the models' scale-to-zero (modules/inference): checks every few
# minutes that an idle endpoint really released its GPU, and finishes the job
# when auto scaling did not. See lambda/watchdog.py. Same shape as terraform/modules/rds-autostop.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account  = data.aws_caller_identity.current.account_id
  region   = data.aws_region.current.name
  endpoint = "arn:aws:sagemaker:${local.region}:${local.account}:endpoint/${var.endpoint_name}"
  ic       = "arn:aws:sagemaker:${local.region}:${local.account}:inference-component/${var.inference_component_name}"
}

data "archive_file" "watchdog" {
  type        = "zip"
  source_file = "${path.module}/lambda/watchdog.py"
  output_path = "${path.root}/.terraform/build/model_watchdog.zip"
}

data "aws_iam_policy_document" "assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "watchdog" {
  name               = "alibi-model-watchdog-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = { Project = "alibi", Environment = var.environment }
}

resource "aws_iam_role_policy_attachment" "basic_execution" {
  role       = aws_iam_role.watchdog.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "watchdog" {
  statement {
    sid    = "ScaleDownAndDeleteTheEndpoint"
    effect = "Allow"
    actions = [
      "sagemaker:DescribeEndpoint",
      "sagemaker:DescribeInferenceComponent",
      "sagemaker:UpdateInferenceComponentRuntimeConfig",
      "sagemaker:DeleteEndpoint",
    ]
    resources = [local.endpoint, local.ic]
  }

  # Neither CloudWatch read API supports resource-level permissions.
  statement {
    sid       = "ReadInvocations"
    effect    = "Allow"
    actions   = ["cloudwatch:GetMetricStatistics", "cloudwatch:ListMetrics"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "watchdog" {
  name   = "alibi-model-watchdog-${var.environment}"
  role   = aws_iam_role.watchdog.id
  policy = data.aws_iam_policy_document.watchdog.json
}

resource "aws_lambda_function" "watchdog" {
  function_name    = "alibi-model-watchdog-${var.environment}"
  role             = aws_iam_role.watchdog.arn
  runtime          = "python3.12"
  handler          = "watchdog.handler"
  filename         = data.archive_file.watchdog.output_path
  source_code_hash = data.archive_file.watchdog.output_base64sha256
  architectures    = ["arm64"]
  memory_size      = 128
  timeout          = 30

  environment {
    variables = {
      ENDPOINT_NAME         = var.endpoint_name
      IC_NAME               = var.inference_component_name
      IDLE_MINUTES          = tostring(var.idle_minutes)
      HARD_IDLE_MINUTES     = tostring(var.hard_idle_minutes)
      DELETE_STUCK_ENDPOINT = tostring(var.delete_stuck_endpoint)
    }
  }

  tags = { Project = "alibi", Environment = var.environment }
}

resource "aws_cloudwatch_event_rule" "check" {
  name                = "alibi-model-watchdog-${var.environment}"
  description         = "Makes sure the idle models' endpoint released its GPU (scale-to-zero backstop)."
  schedule_expression = "rate(${var.check_rate_minutes} minutes)"
  tags                = { Project = "alibi", Environment = var.environment }
}

resource "aws_cloudwatch_event_target" "check" {
  rule = aws_cloudwatch_event_rule.check.name
  arn  = aws_lambda_function.watchdog.arn
}

resource "aws_lambda_permission" "check" {
  statement_id  = "AllowEventBridgeWatchdog"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.watchdog.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.check.arn
}
