# ingest-pipeline — Module B's data plane and its two-stage worker.
#
# The shape of this module is dictated by one fact: a Lambda attached to the VPC
# can reach the private RDS instance but has NO internet route (there is no NAT
# gateway), and a Lambda outside the VPC has internet but cannot reach RDS. So
# the work is cut along exactly that seam instead of paying $32/mo for a NAT or
# $7-15/mo for an interface endpoint:
#
#   S3 ObjectCreated → SQS → PROCESSOR (no VPC, container image)
#                              parses the file, calls the model on Bedrock,
#                              writes Parquet, deletes the raw document
#                            → invokes COMMITTER (in VPC, zip)
#                              writes the control-plane rows in Postgres
#
# See app/services/ingestion.py, which
# implements exactly this split and is tested end-to-end locally.
#
# The one piece of shared infrastructure both halves need is the S3 Gateway VPC
# Endpoint: it is what lets the in-VPC functions (committer, and the API Lambda
# running DuckDB over Parquet) reach S3 at all — and it is free, unlike every
# other way of giving a VPC Lambda access to an AWS service.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  suffix         = data.aws_caller_identity.current.account_id
  bucket_name    = "alibi-ingest-${var.environment}-${local.suffix}"
  processor_name = "alibi-ingest-processor-${var.environment}"
  committer_name = "alibi-ingest-committer-${var.environment}"
  ecr_name       = "alibi-ingest-processor-${var.environment}"
  tags           = { Project = "alibi", Environment = var.environment, Module = "ingest" }
}

# ── Data-plane bucket ──────────────────────────────────────────────────────
# A NEW bucket, deliberately not ML's alibi-datasets-*: that one holds the
# BIRD/Spider training corpora and already owns a bucket notification on the
# `uploads/` prefix (notifications are authoritative per bucket, so a second
# one would silently replace the first). Separate buckets also keep the
# lifecycle rules independent — user data and training corpora expire on very
# different schedules.

resource "aws_s3_bucket" "ingest" {
  bucket = local.bucket_name
  tags   = merge(local.tags, { Purpose = "user-data-plane" })
}

resource "aws_s3_bucket_public_access_block" "ingest" {
  bucket                  = aws_s3_bucket.ingest.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "ingest" {
  bucket = aws_s3_bucket.ingest.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

# The browser PUTs the file straight here with a presigned URL, so the bucket
# itself must accept a cross-origin PUT. Without this the upload fails in the
# browser while succeeding from curl — the classic confusing version.
resource "aws_s3_bucket_cors_configuration" "ingest" {
  bucket = aws_s3_bucket.ingest.id

  cors_rule {
    allowed_methods = ["PUT", "GET", "HEAD"]
    allowed_origins = split(",", var.allowed_origins)
    allowed_headers = ["*"]
    # The browser needs to read the ETag back to confirm the upload.
    expose_headers  = ["ETag"]
    max_age_seconds = 3000
  }
}

# Raw documents are deleted by the processor as soon as it succeeds — the
# product rule is that ALIBI keeps the table, not the document. This rule is the
# backstop for the *failure* path, where the object is deliberately kept so the
# user can retry. Seven days is how long "reintentar" stays honest.
resource "aws_s3_bucket_lifecycle_configuration" "ingest" {
  bucket = aws_s3_bucket.ingest.id

  rule {
    id     = "expire-raw-uploads"
    status = "Enabled"
    filter { prefix = "uploads/" }
    expiration { days = var.raw_retention_days }
  }

  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload { days_after_initiation = 7 }
  }
}

# ── Work queue ─────────────────────────────────────────────────────────────

resource "aws_sqs_queue" "dlq" {
  name = "alibi-ingest-dlq-${var.environment}"
  # 14 days, the maximum. A message here is a bug report, and it should still
  # be there when someone finally goes looking.
  message_retention_seconds = 1209600
  tags                      = local.tags
}

resource "aws_sqs_queue" "jobs" {
  name = "alibi-ingest-${var.environment}"
  # Must be >= the processor's timeout, or SQS redelivers a message that is
  # still being worked on and the file gets processed twice.
  visibility_timeout_seconds = var.processor_timeout_seconds
  message_retention_seconds  = 86400

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    # Three attempts. A transient S3 read deserves a retry; a file the parser
    # genuinely cannot read is acknowledged by the handler and never lands here
    # (see the failure semantics in app/ingest/lambda_handler.py).
    maxReceiveCount = 3
  })

  tags = local.tags
}

data "aws_iam_policy_document" "queue" {
  statement {
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.jobs.arn]

    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_s3_bucket.ingest.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "jobs" {
  queue_url = aws_sqs_queue.jobs.id
  policy    = data.aws_iam_policy_document.queue.json
}

resource "aws_s3_bucket_notification" "ingest" {
  bucket = aws_s3_bucket.ingest.id

  queue {
    queue_arn = aws_sqs_queue.jobs.arn
    events    = ["s3:ObjectCreated:*"]
    # Only raw uploads. Without this prefix the processor's own Parquet writes
    # would re-trigger it — an infinite loop that bills by the invocation.
    filter_prefix = "uploads/"
  }

  depends_on = [aws_sqs_queue_policy.jobs]
}

# ── Container registry (stage 1) ───────────────────────────────────────────
# The processor ships as an image, not a zip: pandas + pyarrow + pdfplumber +
# openpyxl + python-docx blow past Lambda's 250 MB unzipped limit. The image
# limit is 10 GB. (The API Lambda stays a zip precisely because it never parses
# a spreadsheet — it only reads the resulting Parquet with DuckDB.)

resource "aws_ecr_repository" "processor" {
  name                 = local.ecr_name
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration { scan_on_push = true }
  tags = local.tags
}

resource "aws_ecr_lifecycle_policy" "processor" {
  repository = aws_ecr_repository.processor.name
  policy = jsonencode({
    rules = [
      # `bootstrap` is the tag `aws_lambda_function.processor` pins at
      # CreateFunction time, so it must outlive every CI image: it is the only
      # thing that lets the function be recreated from nothing. A single
      # `tagStatus = "any"` rule expired it on 2026-08-28 — it is the OLDEST
      # image by push date the moment CI pushes a 5th, and once it was gone a
      # deploy that had to recreate the function failed with "Source image ...
      # does not exist". Priority 1 fences it off; the count rule below only
      # ever sees the CI-pushed SHA tags.
      {
        rulePriority = 1
        description  = "Never expire the bootstrap tag - Terraform pins it to create the function."
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["bootstrap"]
          countType     = "imageCountMoreThan"
          countNumber   = 1
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep the last 5 images; older ones are just storage cost."
        selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 5 }
        action       = { type = "expire" }
      },
    ]
  })
}

# ── IAM ────────────────────────────────────────────────────────────────────

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

# --- processor (stage 1): S3 + SQS + invoke the committer -------------------

resource "aws_iam_role" "processor" {
  name               = "alibi-ingest-processor-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "processor_basic" {
  role       = aws_iam_role.processor.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "processor" {
  statement {
    sid       = "ReadWriteDataPlane"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.ingest.arn, "${aws_s3_bucket.ingest.arn}/*"]
  }

  statement {
    sid    = "ConsumeQueue"
    effect = "Allow"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
    ]
    resources = [aws_sqs_queue.jobs.arn]
  }

  statement {
    sid       = "HandOffToCommitter"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.committer.arn]
  }

  # Semantic inference + column embeddings on Bedrock (IAM, no key). Reachable
  # at $0 precisely because this function is OUTSIDE the VPC — an in-VPC
  # function would need a bedrock-runtime interface endpoint (~$7/mo).
  # Region-wildcarded foundation models because a cross-region inference
  # profile may route the call to any of several regions.
  dynamic "statement" {
    for_each = var.schema_inference_provider == "bedrock" ? [1] : []
    content {
      sid     = "InvokeBedrockModels"
      effect  = "Allow"
      actions = ["bedrock:InvokeModel"]
      resources = [
        "arn:aws:bedrock:*::foundation-model/*",
        "arn:aws:bedrock:*:${data.aws_caller_identity.current.account_id}:inference-profile/*",
      ]
    }
  }
}

resource "aws_iam_role_policy" "processor" {
  name   = "alibi-ingest-processor-${var.environment}"
  role   = aws_iam_role.processor.id
  policy = data.aws_iam_policy_document.processor.json
}

# --- committer (stage 2): S3 read + VPC ------------------------------------

resource "aws_iam_role" "committer" {
  name               = "alibi-ingest-committer-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "committer_basic" {
  role       = aws_iam_role.committer.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "committer_vpc" {
  role       = aws_iam_role.committer.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

data "aws_iam_policy_document" "committer" {
  # Read-only: stage 2 reads `result.json` and writes Postgres. It never touches
  # the Parquet, which stage 1 already wrote.
  statement {
    sid       = "ReadHandoff"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.ingest.arn, "${aws_s3_bucket.ingest.arn}/*"]
  }
}

resource "aws_iam_role_policy" "committer" {
  name   = "alibi-ingest-committer-${var.environment}"
  role   = aws_iam_role.committer.id
  policy = data.aws_iam_policy_document.committer.json
}

# ── S3 Gateway VPC Endpoint ────────────────────────────────────────────────
# Without this, an in-VPC Lambda cannot reach S3 at all — there is no NAT and
# no internet route. It is what makes the committer able to read result.json
# and, more importantly, what lets the API Lambda run DuckDB over Parquet.
#
# A *gateway* endpoint (not an interface endpoint): it costs nothing, has no
# hourly charge and no per-GB charge. It works by adding a prefix-list route to
# the VPC's route tables, which is also why it only supports S3 and DynamoDB.

data "aws_vpc" "default" {
  default = true
}

data "aws_route_tables" "default" {
  vpc_id = data.aws_vpc.default.id
}

resource "aws_vpc_endpoint" "s3" {
  count             = var.create_s3_endpoint ? 1 : 0
  vpc_id            = data.aws_vpc.default.id
  service_name      = "com.amazonaws.${data.aws_region.current.name}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = data.aws_route_tables.default.ids

  tags = merge(local.tags, { Name = "alibi-s3-gateway-${var.environment}" })
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_security_group" "committer" {
  name        = "alibi-ingest-committer-${var.environment}"
  description = "Stage-2 committer - egress to RDS and to S3 via the gateway endpoint."
  vpc_id      = data.aws_vpc.default.id

  egress {
    description = "Postgres to the RDS instance inside the VPC"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.default.cidr_block]
  }

  egress {
    description = "S3 through the gateway endpoint (still HTTPS, still in-VPC)"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = local.tags
}

# ── Stage 1: processor (container image, NO VPC) ───────────────────────────

resource "aws_lambda_function" "processor" {
  function_name = local.processor_name
  role          = aws_iam_role.processor.arn
  package_type  = "Image"
  # `:bootstrap` until CI pushes a real tag. The image must exist before the
  # function can be created, which is why deploying this is a two-step dance —
  # see the runbook in README.md.
  image_uri = "${aws_ecr_repository.processor.repository_url}:${var.processor_image_tag}"

  timeout = var.processor_timeout_seconds
  # Lambda scales CPU with memory, and this is CPU-bound pandas/pyarrow work:
  # more memory is genuinely faster per invocation, not just roomier.
  memory_size = var.processor_memory_mb

  environment {
    variables = {
      ALIBI_ENVIRONMENT               = var.environment
      ALIBI_OBJECT_STORE              = "s3"
      ALIBI_INGEST_BUCKET             = aws_s3_bucket.ingest.id
      ALIBI_AWS_REGION                = data.aws_region.current.name
      ALIBI_COMMITTER_FUNCTION_NAME   = local.committer_name
      ALIBI_MAX_ROWS_PER_TABLE        = tostring(var.max_rows_per_table)
      ALIBI_SCHEMA_INFERENCE_PROVIDER = var.schema_inference_provider
      ALIBI_SCHEMA_INFERENCE_MODEL_ID = var.schema_inference_model_id
      ALIBI_EMBEDDING_MODEL_ID        = var.embedding_model_id
      ALIBI_EMBEDDING_DIMENSIONS      = tostring(var.embedding_dimensions)
    }
  }

  # Deliberately NO vpc_config. This function calls Bedrock; in the VPC it would
  # have no route to it without a NAT or an interface endpoint. That is the whole
  # reason for the split.

  tags = merge(local.tags, { Stage = "1-processor" })

  lifecycle {
    # CI pushes a new image and updates the function directly, so Terraform
    # must not fight it back to the tag recorded at apply time.
    ignore_changes = [image_uri]
  }
}

resource "aws_lambda_event_source_mapping" "jobs" {
  event_source_arn = aws_sqs_queue.jobs.arn
  function_name    = aws_lambda_function.processor.arn
  # One file per invocation. Batching would mean a single unreadable file
  # failing its nine healthy neighbours.
  batch_size                         = 1
  maximum_batching_window_in_seconds = 0
}

# ── Stage 2: committer (zip, IN VPC) ───────────────────────────────────────
# Reuses the API's own deployment zip — same package, different handler. There
# is no second build: the code is already there, and the committer imports the
# same repositories the API does, so they cannot drift apart.

resource "aws_lambda_function" "committer" {
  function_name = local.committer_name
  role          = aws_iam_role.committer.arn
  runtime       = "python3.12"
  handler       = "app.ingest.lambda_handler.committer_handler"
  timeout       = 60
  memory_size   = 512

  s3_bucket = var.backend_code_bucket
  s3_key    = "code/backend.zip"

  environment {
    variables = {
      ALIBI_ENVIRONMENT   = var.environment
      ALIBI_OBJECT_STORE  = "s3"
      ALIBI_INGEST_BUCKET = aws_s3_bucket.ingest.id
      ALIBI_AWS_REGION    = data.aws_region.current.name
      ALIBI_DATABASE_URL  = var.database_url
    }
  }

  vpc_config {
    subnet_ids         = data.aws_subnets.default.ids
    security_group_ids = [aws_security_group.committer.id]
  }

  depends_on = [aws_iam_role_policy_attachment.committer_vpc]
  tags       = merge(local.tags, { Stage = "2-committer" })

  lifecycle {
    # backend-deploy.yml updates this function's code alongside the API's,
    # from the same zip.
    ignore_changes = [s3_key]
  }
}

# NOTE: the API Lambda's permission to re-invoke the processor on a retry is
# granted on the API's OWN role (an identity-based policy in the api-lambda
# module), not as a resource policy here. Within one account either works, but
# the identity-based direction keeps these two modules independent — otherwise
# each would need an output of the other and the graph would be circular.
