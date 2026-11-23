# Persisted overrides for routine applies (both local bootstrap runs and
# .github/workflows/infra-deploy.yml, which does a plain `terraform apply`
# with no `-var` flags of its own — this file is what it actually reads).
#
# ingest_enabled: Module B's data plane (S3 + SQS + the two-stage worker).
# Left at `false` until the ECR repo + a first image exist (see
# backend/terraform/modules/ingest-pipeline/README.md); infra-deploy.yml
# now bootstraps that image itself, so this only needed to be true once
# after the ECR repo was created.
ingest_enabled = true

# CloudFront's URL wasn't known until the first frontend apply, and it CHANGES
# every time the distribution is recreated (destroy-all/deploy-all) — it was
# d3coqks17hkgoe until 2026-08-30. Both origins must stay listed: localhost for
# local dev, CloudFront for the deployed API's CORS check.
#
# The ingest bucket no longer depends on this value being current: main.tf
# appends module.frontend's live domain to what it passes to module.ingest, so a
# browser upload straight to S3 keeps working across a recreate. This value is
# still what API Gateway's own CORS allows, so keep it in sync anyway.
allowed_origins = "http://localhost:5173,https://d1noq9o5f32nw5.cloudfront.net,https://alibi.com.ar,https://www.alibi.com.ar"

# Custom domain (dns.tf). domain_name creates the Route 53 zone, whose
# `dns_name_servers` go into nic.ar; domain_delegated is true once
# `nslookup -type=NS alibi.com.ar` shows the AWS servers.
domain_name      = "alibi.com.ar"
domain_delegated = true

# ML budget alert recipient. Persisted so a local `terraform apply` never
# prompts for it. NOTE: a tfvars value outranks the TF_VAR_alert_email that
# infra-deploy.yml/infra-destroy.yml export from the ALIBI_ALERT_EMAIL repo
# variable, so this file is now the single source of truth for both.
alert_email = "alibi.pfi@gmail.com"

# Modules A and C on the fine-tuned adapters (SageMaker endpoint model-a-dev,
# scales to zero). Module C is on here so the
# dev environment can be tested end to end; whether it SHIPS is still decided by
# model-c-dev-eval-002. Back to Bedrock: delete these three lines and apply.
text2sql_provider      = "sagemaker"
text2sql_prompt_format = "qwen-bird-spider-v1"
visualization_provider = "sagemaker"

# Module B's optional semantic enrichment (business column names, header
# arbitration, column embeddings) on Bedrock. Model ids keep their defaults in
# variables.tf. Back to deterministic-only: delete this line and apply.
schema_inference_provider = "bedrock"
