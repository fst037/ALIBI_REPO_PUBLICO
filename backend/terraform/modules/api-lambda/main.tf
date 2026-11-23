# api-lambda — FastAPI app running on Lambda behind an API Gateway HTTP API.
#
# The Lambda code (FastAPI + Mangum + deps, built for manylinux x86_64) is
# zipped and uploaded to the code bucket below, then referenced by S3 key —
# same pattern as ML/terraform/modules/ingestion-lambda. Terraform does NOT
# zip Python deps itself (that crashed the Windows-hosted AWS provider for
# ML's Lambda Layer). The zip is built by
# `build/build_api_zip.ps1` for local first-time deploys, and by
# `.github/workflows/backend-deploy.yml` (Ubuntu runner) on every push.
#
# HTTP API (API Gateway v2) is used instead of REST API (v1): cheaper,
# lower-latency, and its Lambda proxy integration is a straight match for
# Mangum's ASGI adapter.

data "aws_caller_identity" "current" {}

locals {
  account_suffix     = data.aws_caller_identity.current.account_id
  fn_name            = "alibi-backend-${var.environment}"
  llm_bridge_fn_name = "alibi-llm-bridge-${var.environment}"
  run_worker_fn_name = "alibi-run-worker-${var.environment}"
  # Answer runs need the models, so the worker exists exactly when they do.
  run_worker_enabled = var.text2sql_enabled
  code_bucket        = "alibi-backend-code-${var.environment}-${local.account_suffix}"
  # The waker is invoked through aws_vpc_endpoint.lambda, which only exists
  # alongside the LLM bridge — without it the invoke has no network path.
  db_waker_enabled = var.db_enabled && var.text2sql_enabled && var.db_waker_function_name != ""
}

# ── Code bucket ────────────────────────────────────────────────────────────

resource "aws_s3_bucket" "code" {
  bucket = local.code_bucket
  tags   = { Project = "alibi", Environment = var.environment, Purpose = "backend-lambda-code" }
}

resource "aws_s3_bucket_public_access_block" "code" {
  bucket                  = aws_s3_bucket.code.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "code" {
  bucket = aws_s3_bucket.code.id
  versioning_configuration { status = "Enabled" }
}

# ── Lambda execution role ─────────────────────────────────────────────────

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "alibi-backend-lambda-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
  tags               = { Project = "alibi", Environment = var.environment }
}

resource "aws_iam_role_policy_attachment" "lambda_basic_execution" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Lets the Lambda service manage ENIs for VPC access (CreateNetworkInterface,
# DescribeNetworkInterfaces, DeleteNetworkInterface). Only attached when the
# Lambda joins the VPC to reach the database.
resource "aws_iam_role_policy_attachment" "lambda_vpc_access" {
  count      = var.db_enabled ? 1 : 0
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# ── VPC + database wiring (only when db_enabled) ───────────────────────────
# The RDS instance is private and reached over a direct socket, so the Lambda
# must join the same (default) VPC. The RDS security group already admits the
# whole VPC CIDR on 5432, so no RDS-side change is needed — the Lambda just
# needs to be in the VPC with egress to the database.

data "aws_vpc" "default" {
  count   = var.db_enabled ? 1 : 0
  default = true
}

data "aws_subnets" "default" {
  count = var.db_enabled ? 1 : 0
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default[0].id]
  }
}

data "aws_db_instance" "alibi" {
  count                  = var.db_enabled ? 1 : 0
  db_instance_identifier = "alibi-${var.environment}"
}

# The AWS-managed master-password secret (rds!db-*). Read at apply time to build
# the connection string (see the db_enabled trade-off in variables.tf).
data "aws_secretsmanager_secret_version" "db" {
  count     = var.db_enabled ? 1 : 0
  secret_id = data.aws_db_instance.alibi[0].master_user_secret[0].secret_arn
}

resource "aws_security_group" "lambda" {
  count       = var.db_enabled ? 1 : 0
  name        = "alibi-backend-lambda-${var.environment}"
  description = "Backend Lambda - egress to the RDS instance inside the VPC."
  vpc_id      = data.aws_vpc.default[0].id

  egress {
    description = "Postgres to the RDS instance inside the VPC"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.default[0].cidr_block]
  }

  # The VPC's S3 Gateway Endpoint (ingest-pipeline/main.tf's aws_vpc_endpoint.s3)
  # adds the route, but a route alone isn't enough — this security group's
  # egress rules are the actual allow-list, and until now it only opened 5432.
  # Everything else (including DuckDB's httpfs reading Parquet over S3) was
  # silently dropped, which looks like a hang, not a clean connection error —
  # that's the 29s Lambda timeout on /preview. Scoped to the S3 prefix list
  # (the gateway endpoint's own mechanism) rather than 0.0.0.0/0.
  dynamic "egress" {
    for_each = var.ingest_enabled ? [1] : []
    content {
      description     = "HTTPS to S3 via the gateway VPC endpoint (DuckDB reading Parquet)"
      from_port       = 443
      to_port         = 443
      protocol        = "tcp"
      prefix_list_ids = [data.aws_prefix_list.s3[0].id]
    }
  }

  # Reaching the LLM bridge Lambda (app/llm_bridge.py) — see
  # aws_vpc_endpoint.lambda below. References that endpoint's own security
  # group rather than the other way around: the endpoint's ingress is scoped
  # to the VPC CIDR instead of back to this SG, specifically so this doesn't
  # become a two-way SG reference (a real Terraform dependency cycle, since
  # both would then be inline blocks on resources depending on each other's id).
  dynamic "egress" {
    for_each = var.text2sql_enabled ? [1] : []
    content {
      description     = "HTTPS to the Lambda service Interface VPC Endpoint (Module A / orchestrator bridge)"
      from_port       = 443
      to_port         = 443
      protocol        = "tcp"
      security_groups = [aws_security_group.lambda_bridge_endpoint[0].id]
    }
  }

  tags = { Project = "alibi", Environment = var.environment }
}

data "aws_prefix_list" "s3" {
  count = var.ingest_enabled ? 1 : 0
  name  = "com.amazonaws.${var.aws_region}.s3"
}

# ── LLM bridge (Module A + orchestrator, called from outside the VPC) ──────
# This Lambda has no route to Bedrock once db_enabled attaches it to the VPC, so
# the model call happens in a second, non-VPC Lambda
# (aws_lambda_function.llm_bridge below) — same two-stage split the ingestion
# pipeline already uses for the file parse. (A bedrock-runtime interface
# endpoint would also work, at ~$7/month more: this Lambda endpoint is paid for
# anyway, since the RDS waker is invoked through it too.) Reaching that Lambda from inside
# the VPC still needs an AWS API call (`lambda:InvokeFunction`), which needs a
# network path of its own: this Interface VPC Endpoint, the cheaper of the two
# options considered (~$7.30/mo for one AZ vs. a NAT gateway's ~$32/mo).

resource "aws_security_group" "lambda_bridge_endpoint" {
  count       = var.text2sql_enabled && var.db_enabled ? 1 : 0
  name        = "alibi-llm-bridge-endpoint-${var.environment}"
  description = "Lambda service Interface VPC Endpoint - inbound HTTPS from inside the VPC."
  vpc_id      = data.aws_vpc.default[0].id

  # Scoped to the VPC CIDR, not to aws_security_group.lambda's id: that SG's
  # own egress rule (above) already references THIS security group, and a
  # rule here pointing back at it would make the two mutually dependent —
  # Terraform rejects that as a cycle.
  ingress {
    description = "HTTPS from inside the VPC"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.default[0].cidr_block]
  }

  tags = { Project = "alibi", Environment = var.environment }
}

resource "aws_vpc_endpoint" "lambda" {
  count             = var.text2sql_enabled && var.db_enabled ? 1 : 0
  vpc_id            = data.aws_vpc.default[0].id
  service_name      = "com.amazonaws.${var.aws_region}.lambda"
  vpc_endpoint_type = "Interface"
  # One AZ only — the cheaper, accepted trade-off for a dev/thesis
  # environment; a subnet outage here means Module
  # A/the orchestrator 503 until it recovers, the same failure mode this
  # already has today when unconfigured, not a new one.
  subnet_ids          = [data.aws_subnets.default[0].ids[0]]
  security_group_ids  = [aws_security_group.lambda_bridge_endpoint[0].id]
  private_dns_enabled = true

  tags = { Project = "alibi", Environment = var.environment, Name = "alibi-lambda-interface-${var.environment}" }
}

data "aws_iam_policy_document" "llm_bridge_invoke" {
  count = var.text2sql_enabled && var.db_enabled ? 1 : 0

  # Granted on the CALLER's role, not as a resource policy on the bridge —
  # same reasoning as `ingest_access`'s RetryProcessing statement above: it
  # keeps this module's two Lambda resources independent of each other.
  statement {
    sid       = "InvokeLlmBridge"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.llm_bridge[0].arn]
  }
}

resource "aws_iam_role_policy" "llm_bridge_invoke" {
  count  = var.text2sql_enabled && var.db_enabled ? 1 : 0
  name   = "alibi-backend-llm-bridge-${var.environment}"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.llm_bridge_invoke[0].json
}

# Same caller-side grant as llm_bridge_invoke, for terraform/modules/
# rds-autostop's function. Built from the name: it lives in the database root,
# which shares no state with this one.
data "aws_iam_policy_document" "db_waker_invoke" {
  count = local.db_waker_enabled ? 1 : 0

  statement {
    sid       = "InvokeDbWaker"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = ["arn:aws:lambda:${var.aws_region}:${local.account_suffix}:function:${var.db_waker_function_name}"]
  }
}

resource "aws_iam_role_policy" "db_waker_invoke" {
  count  = local.db_waker_enabled ? 1 : 0
  name   = "alibi-backend-db-waker-${var.environment}"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.db_waker_invoke[0].json
}

resource "aws_iam_role" "llm_bridge" {
  count              = var.text2sql_enabled && var.db_enabled ? 1 : 0
  name               = "alibi-llm-bridge-lambda-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
  tags               = { Project = "alibi", Environment = var.environment }
}

resource "aws_iam_role_policy_attachment" "llm_bridge_basic_execution" {
  count      = var.text2sql_enabled && var.db_enabled ? 1 : 0
  role       = aws_iam_role.llm_bridge[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# No vpc_config: this is the whole point — normal AWS-API egress, so it can
# reach Bedrock directly. Same zip as aws_lambda_function.api (one build, two
# functions, no drift — matching Module B's stage-2 committer), different
# handler.
#
# No model env vars, deliberately: app/llm_bridge.py's handler builds its
# EndpointConfig/LlmConfig (model id, region, budgets) entirely from the invoke
# payload, never from its own settings — the caller (aws_lambda_function.api)
# decides which model answers. There are no credentials at all: Bedrock
# authorises this function's IAM role (aws_iam_role_policy.bedrock_invoke_bridge).
#
# The one exception is SMTP (`kind: "mail"`, app/core/mailer.py): the API Lambda
# cannot reach the mail server from the VPC either, so the verification-code
# email leaves from here, and the mailbox credentials live in this environment.
resource "aws_lambda_function" "llm_bridge" {
  count         = var.text2sql_enabled && var.db_enabled ? 1 : 0
  function_name = local.llm_bridge_fn_name
  role          = aws_iam_role.llm_bridge[0].arn
  runtime       = "python3.12"
  handler       = "app.llm_bridge.bridge_handler"
  timeout       = var.llm_bridge_timeout_seconds
  memory_size   = var.lambda_memory_mb

  s3_bucket = aws_s3_bucket.code.id
  s3_key    = "code/backend.zip"

  # Only when a mailbox is configured: an empty block would diff on every plan.
  # Keyed on the user, not the password: a value derived from a sensitive
  # variable is not allowed in for_each.
  dynamic "environment" {
    for_each = var.smtp_user != "" ? [1] : []
    content {
      variables = local.smtp_env
    }
  }

  tags = { Project = "alibi", Environment = var.environment, Purpose = "llm-bridge" }
}

# Bedrock. Granted to whichever function makes the model call: the bridge in the
# VPC topology, this API Lambda when it is not VPC-attached (local-like deploys
# with db_enabled = false). Converse is authorised as bedrock:InvokeModel.
#
# Scoped to model invocation, not to model ids: the ids are configuration
# (text2sql_model_id / orchestrator_model_id) and a cross-region inference
# profile routes each call to a foundation model in any of several regions, so
# the foundation-model ARNs are region-wildcarded. Invoking is the only Bedrock
# action granted — no model access management, no customisation.
data "aws_iam_policy_document" "bedrock_invoke" {
  count = var.text2sql_enabled ? 1 : 0

  statement {
    sid     = "InvokeBedrockModels"
    effect  = "Allow"
    actions = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = [
      "arn:aws:bedrock:*::foundation-model/*",
      "arn:aws:bedrock:*:${local.account_suffix}:inference-profile/*",
    ]
  }
}

resource "aws_iam_role_policy" "bedrock_invoke_bridge" {
  count  = var.text2sql_enabled && var.db_enabled ? 1 : 0
  name   = "alibi-llm-bridge-bedrock-${var.environment}"
  role   = aws_iam_role.llm_bridge[0].id
  policy = data.aws_iam_policy_document.bedrock_invoke[0].json
}

# The fine-tuned models' endpoint, same split as Bedrock: the bridge makes the
# call in the VPC topology, this API Lambda otherwise. Invoke it, and — because
# it scales to zero — read and raise its inference component's copy count
# (app/core/sagemaker.py `wake`). Scoped to the one endpoint and the one IC.
locals {
  fine_tuned_enabled = var.text2sql_enabled && var.fine_tuned_endpoint_name != ""
}

data "aws_iam_policy_document" "fine_tuned_invoke" {
  count = local.fine_tuned_enabled ? 1 : 0

  statement {
    sid     = "InvokeFineTunedModels"
    effect  = "Allow"
    actions = ["sagemaker:InvokeEndpoint"]
    resources = compact([
      "arn:aws:sagemaker:${var.aws_region}:${local.account_suffix}:endpoint/${lower(var.fine_tuned_endpoint_name)}",
      var.fine_tuned_inference_component == "" ? "" : "arn:aws:sagemaker:${var.aws_region}:${local.account_suffix}:inference-component/${lower(var.fine_tuned_inference_component)}",
    ])
  }

  dynamic "statement" {
    for_each = var.fine_tuned_inference_component == "" ? [] : [1]
    content {
      sid    = "WakeFineTunedModels"
      effect = "Allow"
      actions = [
        "sagemaker:DescribeInferenceComponent",
        "sagemaker:UpdateInferenceComponentRuntimeConfig",
      ]
      resources = ["arn:aws:sagemaker:${var.aws_region}:${local.account_suffix}:inference-component/${lower(var.fine_tuned_inference_component)}"]
    }
  }

  # Read-only: how many instances the endpoint has, to tell "still loading"
  # from "no GPU is coming" while the model wakes.
  dynamic "statement" {
    for_each = var.fine_tuned_inference_component == "" ? [] : [1]
    content {
      sid       = "SeeFineTunedEndpointInstances"
      effect    = "Allow"
      actions   = ["sagemaker:DescribeEndpoint"]
      resources = ["arn:aws:sagemaker:${var.aws_region}:${local.account_suffix}:endpoint/${lower(var.fine_tuned_endpoint_name)}"]
    }
  }
}

resource "aws_iam_role_policy" "fine_tuned_invoke_bridge" {
  count  = local.fine_tuned_enabled && var.db_enabled ? 1 : 0
  name   = "alibi-llm-bridge-sagemaker-${var.environment}"
  role   = aws_iam_role.llm_bridge[0].id
  policy = data.aws_iam_policy_document.fine_tuned_invoke[0].json
}

resource "aws_iam_role_policy" "fine_tuned_invoke_api" {
  count  = local.fine_tuned_enabled && !var.db_enabled ? 1 : 0
  name   = "alibi-backend-sagemaker-${var.environment}"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.fine_tuned_invoke[0].json
}

resource "aws_iam_role_policy" "bedrock_invoke_api" {
  count  = var.text2sql_enabled && !var.db_enabled ? 1 : 0
  name   = "alibi-backend-bedrock-${var.environment}"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.bedrock_invoke[0].json
}

locals {
  db_secret = var.db_enabled ? jsondecode(data.aws_secretsmanager_secret_version.db[0].secret_string) : null

  # libpq keyword/value quoting for the password, which is the ONE field here
  # whose content we do not control: RDS generates 28 characters and guarantees
  # at least one punctuation mark.
  #
  # Unquoted, a libpq value ends at the first space and a backslash is an escape
  # character - so a generated password containing a backslash arrives at the
  # server with it silently removed. The failure is maximally misleading: host,
  # user and sslmode all parse perfectly and the connection is refused with
  # "password authentication failed for user alibi_admin", which reads exactly
  # like a stale password. Quoting the value and escaping backslash and quote is
  # what libpq documents; verified to round-trip through psycopg's conninfo
  # parser, which is what app/db/engine.py uses to read this back.
  db_password = var.db_enabled ? replace(replace(local.db_secret.password, "\\", "\\\\"), "'", "\\'") : ""

  db_dsn = var.db_enabled ? join(" ", [
    "host=${data.aws_db_instance.alibi[0].address}",
    "port=5432",
    "dbname=alibi",
    "user=${local.db_secret.username}",
    "password='${local.db_password}'",
    "sslmode=require",
  ]) : ""
}

# ── Data-plane access (only when ingest_enabled) ───────────────────────────
# Presigning is pure computation — SigV4 needs no API call, which is why the API
# can hand out an upload URL with no network access to S3. But S3 validates the
# *signer's* permissions when the browser actually PUTs, so `s3:PutObject` has to
# be here or every upload 403s at the last moment. `GetObject` is what DuckDB
# uses; `DeleteObject` is deleting a dataset's Parquet.

data "aws_iam_policy_document" "ingest_access" {
  count = var.ingest_enabled ? 1 : 0

  statement {
    sid       = "DataPlaneObjects"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["arn:aws:s3:::${var.ingest_bucket}/*"]
  }

  statement {
    sid       = "DataPlaneList"
    effect    = "Allow"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = ["arn:aws:s3:::${var.ingest_bucket}"]
  }

  # Retry only. Granted on this role rather than as a resource policy on the
  # processor, so the two Terraform modules stay independent of each other.
  statement {
    sid       = "RetryProcessing"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = ["arn:aws:lambda:${var.aws_region}:${local.account_suffix}:function:${var.processor_function_name}"]
  }
}

resource "aws_iam_role_policy" "ingest_access" {
  count  = var.ingest_enabled ? 1 : 0
  name   = "alibi-backend-ingest-${var.environment}"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.ingest_access[0].json
}

# ── Lambda function ────────────────────────────────────────────────────────

# SMTP for the verification code — on whichever function opens the connection:
# the bridge when there is one, this Lambda otherwise (see app/core/mailer.py).
locals {
  # Keyed on the (non-secret) user, so only the password is marked sensitive,
  # not the whole environment map it is merged into.
  smtp_env = var.smtp_user == "" ? {} : {
    ALIBI_SMTP_USER     = var.smtp_user
    ALIBI_SMTP_PASSWORD = var.smtp_password
  }
  # Keyed on a non-sensitive boolean for the same reason as smtp_env.
  mp_env = nonsensitive(var.mp_access_token != "") ? { ALIBI_MP_ACCESS_TOKEN = var.mp_access_token } : {}
}

# Environment shared by the API and the run worker (same code, same settings).
locals {
  app_env = merge(
    {
      ALIBI_ENVIRONMENT     = var.environment
      ALIBI_ALLOWED_ORIGINS = var.allowed_origins
      # Official ARS per USD: plan prices are USD reference values charged in
      # pesos at this rate (app/core/pricing.py).
      ALIBI_USD_ARS_RATE       = tostring(var.usd_ars_rate)
      ALIBI_USD_ARS_RATE_AS_OF = var.usd_ars_rate_as_of
    },
    # "Continuar con Google": the audience ID tokens are checked against.
    var.google_client_id != "" ? { ALIBI_GOOGLE_CLIENT_ID = var.google_client_id } : {},
    # Mercado Pago Checkout Pro: the token the bridge calls Mercado Pago with.
    local.mp_env,
    var.db_enabled ? { ALIBI_DATABASE_URL = local.db_dsn } : {},
    var.ingest_enabled ? {
      ALIBI_OBJECT_STORE = "s3"
      # DuckDB reads Parquet straight out of here, through the S3 Gateway VPC
      # Endpoint (this function is in the VPC and has no other route to S3).
      ALIBI_INGEST_BUCKET = var.ingest_bucket
      ALIBI_AWS_REGION    = var.aws_region
      # Only used by the retry path; the normal flow is started by S3's event.
      ALIBI_JOB_RUNNER              = "lambda"
      ALIBI_PROCESSOR_FUNCTION_NAME = var.processor_function_name
    } : {},
    # Module A + orchestrator model ids — ALWAYS on this Lambda, in BOTH
    # routing modes, never on aws_lambda_function.llm_bridge. This Lambda
    # builds the EndpointConfig/LlmConfig (app/services/text2sql/client.py,
    # app/orchestrator/client.py) from its own settings: in direct mode it
    # calls Bedrock itself, in bridge mode it sends that same config in each
    # invoke. No keys — Bedrock authorises an IAM role. The two are gated
    # together, deliberately: a conversation needs both to answer (Module A
    # writes the query, the orchestrator explains the result), so
    # half-enabling would deploy an API that 503s on every execute.
    var.text2sql_enabled ? {
      ALIBI_TEXT2SQL_PROMPT_FORMAT = var.text2sql_prompt_format
      ALIBI_TEXT2SQL_MODEL_ID      = var.text2sql_model_id
      ALIBI_ORCHESTRATOR_MODEL_ID  = var.orchestrator_model_id
      ALIBI_AWS_REGION             = var.aws_region
      # Per-model token prices for the cost ledger (app/core/usage_meter.py).
      ALIBI_MODEL_PRICES_USD = jsonencode(var.model_prices_usd)
      # The agent harness: planner + the Quick/Deep synthesis models.
      ALIBI_PLANNER_MODEL_ID         = var.planner_model_id
      ALIBI_SYNTHESIS_QUICK_MODEL_ID = var.synthesis_quick_model_id
      ALIBI_SYNTHESIS_DEEP_MODEL_ID  = var.synthesis_deep_model_id
    } : {},
    # The fine-tuned adapters (app/core/sagemaker.py): one endpoint, both
    # modules. The providers say which module uses it; Bedrock stays the
    # default for each until its adapter is chosen to ship.
    var.text2sql_enabled ? {
      ALIBI_TEXT2SQL_PROVIDER      = var.text2sql_provider
      ALIBI_VISUALIZATION_PROVIDER = var.visualization_provider
    } : {},
    var.text2sql_enabled && var.fine_tuned_endpoint_name != "" ? {
      ALIBI_SAGEMAKER_ENDPOINT_NAME       = var.fine_tuned_endpoint_name
      ALIBI_SAGEMAKER_INFERENCE_COMPONENT = var.fine_tuned_inference_component
      ALIBI_MODEL_WAKE_WAIT_SECONDS       = tostring(var.model_wake_wait_seconds)
    } : {},
    # Bridge mode — see the "LLM bridge" resources below. This Lambda calls
    # app/llm_bridge.py (a second, non-VPC Lambda) instead of Bedrock directly,
    # carrying the config built from the model ids above in each invoke.
    # See app/core/llm_bridge_transport.py.
    var.text2sql_enabled && var.db_enabled ? {
      ALIBI_LLM_BRIDGE_FUNCTION_NAME = aws_lambda_function.llm_bridge[0].function_name
    } : local.smtp_env,
    # A failed DB connection asks this function to start a stopped RDS
    # instance and answers 503 db_waking — see app/db/waker.py.
    local.db_waker_enabled ? {
      ALIBI_DB_WAKER_FUNCTION_NAME = var.db_waker_function_name
    } : {},
  )
}

resource "aws_lambda_function" "api" {
  function_name = local.fn_name
  role          = aws_iam_role.lambda.arn
  # python3.12 runs on Amazon Linux 2023 (GLIBC 2.34); python3.11 was on
  # Amazon Linux 2 (GLIBC 2.26), too old for the manylinux_2_28 wheels of
  # bcrypt/psycopg. The build must target manylinux_2_28 to match (see the
  # build script below and backend-deploy.yml).
  runtime     = "python3.12"
  handler     = "app.main.handler"
  timeout     = var.lambda_timeout_seconds
  memory_size = var.lambda_memory_mb

  s3_bucket = aws_s3_bucket.code.id
  s3_key    = "code/backend.zip"

  environment {
    # The run worker reads the same settings; only the API carries the runner
    # switch, since only the API starts runs.
    variables = merge(local.app_env, local.run_worker_enabled ? {
      ALIBI_RUN_RUNNER               = "lambda"
      ALIBI_RUN_WORKER_FUNCTION_NAME = local.run_worker_fn_name
    } : {})
  }

  # A missing model id would look fine at apply time and fail at runtime —
  # caught here rather than after a deploy, because it surfaces as a 503 on
  # every question with nothing in the plan to explain it.
  lifecycle {
    precondition {
      condition = !var.text2sql_enabled || alltrue([
        for id in [var.text2sql_model_id, var.orchestrator_model_id, var.planner_model_id,
        var.synthesis_quick_model_id, var.synthesis_deep_model_id] : id != ""
      ])
      error_message = "text2sql_enabled is true but a model id is empty (text2sql, orchestrator, planner, synthesis_quick, synthesis_deep). Every one is needed to answer a question — Module A writes the queries, the planner chooses them, the synthesis writes the answer — and none falls back, so a half-configured deploy 503s on every question."
    }
  }

  # In the VPC (so it can reach the private RDS) only when db_enabled.
  dynamic "vpc_config" {
    for_each = var.db_enabled ? [1] : []
    content {
      subnet_ids         = data.aws_subnets.default[0].ids
      security_group_ids = [aws_security_group.lambda[0].id]
    }
  }

  # The VPC-access managed policy must be attached before the function is
  # created/updated with vpc_config, or the Lambda service can't manage ENIs.
  depends_on = [aws_iam_role_policy_attachment.lambda_vpc_access]

  tags = { Project = "alibi", Environment = var.environment, Purpose = "backend-api" }
}

# Placeholder zip so the first `terraform apply` has something to point at.
# Lambda's CreateFunction validates that the S3 object is actually a zip, so
# this can't be a plain-text placeholder — it's a minimal real zip archive
# built with the archive provider. CI and the local build script overwrite
# this object afterwards via `aws lambda update-function-code`, which
# Terraform does not track (no s3_object_version pinned), so it never
# causes drift once the real code is deployed.
data "archive_file" "placeholder" {
  type        = "zip"
  output_path = "${path.module}/placeholder.zip"

  source {
    content  = "placeholder"
    filename = "placeholder.txt"
  }
}

resource "aws_s3_object" "placeholder" {
  bucket = aws_s3_bucket.code.id
  key    = "code/backend.zip"
  source = data.archive_file.placeholder.output_path
  etag   = data.archive_file.placeholder.output_md5

  lifecycle {
    ignore_changes = [source, etag]
  }
}

resource "local_file" "build_api_zip" {
  filename        = "${path.root}/build/build_api_zip.ps1"
  file_permission = "0644"
  content         = <<-EOT
    # Auto-generated by Terraform.
    # Packages the FastAPI app + dependencies into a Lambda-compatible .zip
    # and uploads it to the code bucket. Run once before the first deploy;
    # `.github/workflows/backend-deploy.yml` does this on every push after.
    #
    # NOTE: unlike backend-deploy.yml, this script does NOT bundle the DuckDB
    # httpfs extension (app/services/query.py's `_connect`) — that requires
    # actually importing the just-installed duckdb build to fetch its matching
    # extension binary, and here it's a Linux wheel cross-installed onto a
    # Windows machine, not something this script can execute. A dataset
    # preview will 500 (falls back to `INSTALL httpfs`, which can't reach the
    # internet from the Lambda's VPC) until backend-deploy.yml's next run
    # overwrites this zip with one that has it bundled.
    $ErrorActionPreference = "Stop"
    # Resolved via git instead of a fixed "..\..\..\.." hop count, so this
    # script keeps working regardless of which root's build/ directory it's
    # generated into (same fix already applied to
    # ML/terraform/modules/ingestion-lambda's build script).
    $RepoRoot   = (& git rev-parse --show-toplevel) -replace '/', '\'
    $BackendDir = Join-Path $RepoRoot "backend"
    $BuildDir   = Join-Path $BackendDir "build\package"
    $OutZip     = Join-Path $BackendDir "backend.zip"
    $CodeBucket = "${local.code_bucket}"

    Write-Host "[api-zip] installing dependencies for linux x86_64"
    if (Test-Path $BuildDir) { Remove-Item -Recurse -Force $BuildDir }
    New-Item -ItemType Directory -Force $BuildDir | Out-Null
    pip install -r (Join-Path $BackendDir "requirements.txt") `
      --platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64 --target $BuildDir `
      --python-version 3.12 --only-binary=:all: --upgrade

    Write-Host "[api-zip] copying app code"
    Copy-Item -Recurse (Join-Path $BackendDir "app") $BuildDir

    Write-Host "[api-zip] creating $OutZip"
    if (Test-Path $OutZip) { Remove-Item $OutZip }
    Compress-Archive -Path "$BuildDir\*" -DestinationPath $OutZip

    Write-Host "[api-zip] uploading to s3://$CodeBucket/code/backend.zip"
    aws s3 cp $OutZip "s3://$CodeBucket/code/backend.zip" --profile alibi --no-verify-ssl

    Write-Host "[api-zip] updating Lambda function code"
    aws lambda update-function-code --function-name "${local.fn_name}" `
      --s3-bucket $CodeBucket --s3-key "code/backend.zip" `
      --profile alibi --no-verify-ssl | Out-Null

    Write-Host "[api-zip] done"
  EOT
}

# ── API Gateway HTTP API ───────────────────────────────────────────────────

resource "aws_apigatewayv2_api" "api" {
  name          = "alibi-backend-${var.environment}"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = split(",", var.allowed_origins)
    allow_methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    allow_headers = ["*"]
  }

  tags = { Project = "alibi", Environment = var.environment }
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "default" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.api.id
  name        = "$default"
  auto_deploy = true

  tags = { Project = "alibi", Environment = var.environment }
}

resource "aws_lambda_permission" "apigateway" {
  statement_id  = "AllowAPIGatewayInvoke-${var.environment}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}

# ── Answer-run worker (app/run_worker.py) ─────────────────────────────────────
# A question is answered asynchronously: the API records a run, async-invokes
# this function and returns at once; this function has `run_worker_timeout_seconds`
# (15 min) where a request through API Gateway has 29 s — room for the agent
# harness's several queries plus a bigger synthesis model. Same zip, same role,
# same environment and same VPC placement as the API (it reads Postgres and
# reaches the models through the bridge exactly as the API does); different
# handler.
resource "aws_lambda_function" "run_worker" {
  count         = local.run_worker_enabled ? 1 : 0
  function_name = local.run_worker_fn_name
  role          = aws_iam_role.lambda.arn
  runtime       = "python3.12"
  handler       = "app.run_worker.handler"
  timeout       = var.run_worker_timeout_seconds
  memory_size   = var.lambda_memory_mb

  s3_bucket = aws_s3_bucket.code.id
  s3_key    = "code/backend.zip"

  environment {
    variables = local.app_env
  }

  dynamic "vpc_config" {
    for_each = var.db_enabled ? [1] : []
    content {
      subnet_ids         = data.aws_subnets.default[0].ids
      security_group_ids = [aws_security_group.lambda[0].id]
    }
  }

  depends_on = [aws_iam_role_policy_attachment.lambda_vpc_access]

  tags = { Project = "alibi", Environment = var.environment, Purpose = "run-worker" }
}

# The API starts runs. Granted on the caller's role, like llm_bridge_invoke. In
# the VPC topology the invoke goes through aws_vpc_endpoint.lambda, which
# already exists for the bridge.
data "aws_iam_policy_document" "run_worker_invoke" {
  count = local.run_worker_enabled ? 1 : 0

  statement {
    sid       = "InvokeRunWorker"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.run_worker[0].arn]
  }
}

resource "aws_iam_role_policy" "run_worker_invoke" {
  count  = local.run_worker_enabled ? 1 : 0
  name   = "alibi-backend-run-worker-${var.environment}"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.run_worker_invoke[0].json
}
