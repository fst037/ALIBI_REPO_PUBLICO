# Stops the RDS instance after var.idle_minutes without use and starts it again
# on demand — the database's version of the bastion's self-stop. Stopped, the
# instance bills only its storage (~$2.30/mo for 20 GB gp3) instead of
# ~$14/mo; db.t4g.micro and 20 GB are already RDS's floor, so this is the only
# lever left.
#
# One Lambda (lambda/db_power.py), outside the VPC so it reaches the RDS and
# CloudWatch APIs over the internet without a NAT or interface endpoint:
#   - EventBridge calls it every var.check_rate_minutes to stop an idle DB.
#   - The backend API Lambda calls it when a connection fails, through the
#     Lambda interface endpoint it already has for the LLM bridge, and tells
#     the browser the database is waking (503 db_waking).
#
# RDS restarts a stopped instance on its own after 7 days; the idle check
# simply stops it again an hour later.

data "archive_file" "power" {
  type        = "zip"
  source_file = "${path.module}/lambda/db_power.py"
  output_path = "${path.root}/.terraform/build/db_power.zip"
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

resource "aws_iam_role" "power" {
  name               = "alibi-db-power-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = { Project = "alibi", Environment = var.environment }
}

resource "aws_iam_role_policy_attachment" "basic_execution" {
  role       = aws_iam_role.power.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "power" {
  statement {
    sid       = "StartStopDatabase"
    effect    = "Allow"
    actions   = ["rds:DescribeDBInstances", "rds:StartDBInstance", "rds:StopDBInstance"]
    resources = [var.db_instance_arn]
  }

  # Neither API supports resource-level permissions.
  statement {
    sid       = "ReadActivitySignals"
    effect    = "Allow"
    actions   = ["cloudwatch:GetMetricStatistics", "ec2:DescribeInstances"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "power" {
  name   = "alibi-db-power-${var.environment}"
  role   = aws_iam_role.power.id
  policy = data.aws_iam_policy_document.power.json
}

resource "aws_lambda_function" "power" {
  function_name    = "alibi-db-power-${var.environment}"
  role             = aws_iam_role.power.arn
  runtime          = "python3.12"
  handler          = "db_power.handler"
  filename         = data.archive_file.power.output_path
  source_code_hash = data.archive_file.power.output_base64sha256
  architectures    = ["arm64"]
  memory_size      = 128
  timeout          = 15

  environment {
    variables = {
      DB_INSTANCE_ID       = var.db_instance_identifier
      IDLE_MINUTES         = tostring(var.idle_minutes)
      IDLE_MAX_CONNECTIONS = tostring(var.idle_max_connections)
      ACTIVITY_FUNCTIONS   = join(",", var.activity_function_names)
      BASTION_INSTANCE_ID  = var.bastion_instance_id
    }
  }

  tags = { Project = "alibi", Environment = var.environment }
}

resource "aws_cloudwatch_event_rule" "idle_check" {
  name                = "alibi-db-autostop-${var.environment}"
  description         = "Stops the ALIBI RDS instance once it has been idle for ${var.idle_minutes} minutes."
  schedule_expression = "rate(${var.check_rate_minutes} minutes)"
  tags                = { Project = "alibi", Environment = var.environment }
}

resource "aws_cloudwatch_event_target" "idle_check" {
  rule  = aws_cloudwatch_event_rule.idle_check.name
  arn   = aws_lambda_function.power.arn
  input = jsonencode({ action = "stop_if_idle" })
}

resource "aws_lambda_permission" "idle_check" {
  statement_id  = "AllowEventBridgeIdleCheck"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.power.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.idle_check.arn
}
