# Single GitHub Actions OIDC identity for the unified ALIBI infra
# (backend + frontend + ML dev), replacing the three separate per-project
# github-oidc modules (backend/terraform/modules/github-oidc,
# frontend/terraform/modules/github-oidc, ML/terraform/modules/github-oidc).
# This role now also covers the code-push deploys
# (backend-deploy.yml, frontend-deploy.yml) — one role, one repo variable
# (AWS_CI_DEPLOY_ROLE_ARN).
#
# The OIDC provider for token.actions.githubusercontent.com already exists
# in this account (originally created by frontend/terraform/modules/
# github-oidc). Ownership of that resource moved into this module's state
# via `terraform state mv` — it is not created fresh here. If this module is
# ever applied against an account where the provider doesn't exist yet,
# remove the state mv step and let this resource create it.

terraform {
  required_providers {
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
}

data "tls_certificate" "github" {
  url = "https://token.actions.githubusercontent.com/.well-known/openid-configuration"
}

data "aws_caller_identity" "current" {}

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.github.certificates[0].sha1_fingerprint]
}

data "aws_iam_policy_document" "trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [for b in var.allowed_branches : "repo:${var.github_org}/${var.github_repo}:ref:refs/heads/${b}"]
    }
  }
}

resource "aws_iam_role" "ci" {
  name               = "alibi-ci-deploy-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.trust.json
  tags               = { Project = "alibi", Environment = var.environment, Purpose = "unified-ci-deploy" }
}

# Broad-but-scoped: service-level actions restricted to the alibi-* / Alibi*
# resource naming convention used across every module, not account-wide
# admin. Accepted tradeoff for a single-tenant thesis AWS account. S3 bucket deletion is intentionally never granted here,
# so even a mistaken destroy target can't drop a data bucket.
data "aws_iam_policy_document" "ci" {
  statement {
    sid       = "StateBucketList"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [var.state_bucket_arn]
  }

  # Every plan/apply refreshes this resource (it's in state from the
  # migration), so read access is needed on every run, not just when
  # actually changing it.
  statement {
    sid    = "ManageOidcProvider"
    effect = "Allow"
    actions = [
      "iam:GetOpenIDConnectProvider",
      "iam:CreateOpenIDConnectProvider",
      "iam:DeleteOpenIDConnectProvider",
      "iam:UpdateOpenIDConnectProviderThumbprint",
      "iam:TagOpenIDConnectProvider",
      "iam:UntagOpenIDConnectProvider",
    ]
    resources = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com"]
  }

  statement {
    sid    = "StateObjectReadWrite"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = ["${var.state_bucket_arn}/${var.state_key_prefix}*"]
  }

  # terraform/envs/database is a separate, standalone state (its own root,
  # applied by db-deploy.yml) — deliberately not composed into the unified
  # root's state_key_prefix above. Same CI role covers both rather than
  # minting a second OIDC role for one more state file.
  statement {
    sid    = "DatabaseStateObjectReadWrite"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = ["${var.state_bucket_arn}/alibi/database/*"]
  }

  # Manage the app-level S3 buckets' configuration and contents. No
  # s3:DeleteBucket / s3:PutLifecycleConfiguration-based expiry — buckets
  # are never destroyed by the unified workflows.
  statement {
    sid    = "ManageAppBuckets"
    effect = "Allow"
    actions = [
      "s3:CreateBucket",
      "s3:ListBucket",
      "s3:GetBucketLocation",
      "s3:GetBucketAcl",
      "s3:GetBucketPolicy",
      "s3:PutBucketPolicy",
      "s3:DeleteBucketPolicy",
      "s3:GetEncryptionConfiguration",
      "s3:PutEncryptionConfiguration",
      "s3:GetBucketVersioning",
      "s3:PutBucketVersioning",
      "s3:GetBucketPublicAccessBlock",
      "s3:PutBucketPublicAccessBlock",
      "s3:GetBucketNotification",
      "s3:PutBucketNotification",
      "s3:GetBucketTagging",
      "s3:PutBucketTagging",
      "s3:GetLifecycleConfiguration",
      "s3:PutLifecycleConfiguration",
      # The ingest bucket's CORS config (aws_s3_bucket_cors_configuration.ingest,
      # ingest-pipeline/main.tf) needs the write side too — only the read side
      # was granted below, which is why the first ingest apply failed here.
      "s3:PutBucketCORS",
      # The AWS provider's aws_s3_bucket resource refreshes every one of
      # these sub-configs on every plan/apply regardless of whether the
      # config is actually managed by this repo — read-only, granted
      # wholesale to stop the permission whack-a-mole.
      "s3:GetBucketCORS",
      "s3:GetBucketLogging",
      "s3:GetBucketObjectLockConfiguration",
      "s3:GetReplicationConfiguration",
      "s3:GetBucketRequestPayment",
      "s3:GetBucketWebsite",
      "s3:GetAccelerateConfiguration",
      "s3:GetBucketOwnershipControls",
    ]
    resources = ["arn:aws:s3:::alibi-*"]
  }

  statement {
    sid    = "ManageAppBucketObjects"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:GetObjectTagging",
    ]
    resources = ["arn:aws:s3:::alibi-*/*"]
  }

  statement {
    sid    = "ManageLambda"
    effect = "Allow"
    actions = [
      "lambda:CreateFunction",
      "lambda:UpdateFunctionCode",
      "lambda:UpdateFunctionConfiguration",
      "lambda:DeleteFunction",
      "lambda:GetFunction",
      "lambda:GetFunctionConfiguration",
      "lambda:GetFunctionCodeSigningConfig",
      "lambda:ListVersionsByFunction",
      "lambda:AddPermission",
      "lambda:RemovePermission",
      "lambda:GetPolicy",
      "lambda:TagResource",
      "lambda:UntagResource",
      "lambda:ListTags",
      # backend-deploy.yml invokes the function directly with
      # {"_alibi_migrate": true} once per deploy, right after the code
      # update, so the (already VPC-attached) Lambda runs its own
      # `alembic upgrade head` — see app/main.py's `handler`. GetFunction
      # above already covers the "wait for the code update to finish"
      # polling step.
      "lambda:InvokeFunction",
    ]
    resources = ["arn:aws:lambda:*:*:function:alibi-*"]
  }

  statement {
    sid     = "ManageApiGateway"
    effect  = "Allow"
    actions = ["apigateway:*"]
    resources = [
      "arn:aws:apigateway:*::/apis*",
      # Tagging an HTTP API on create/update goes through a separate
      # resource ARN namespace (/tags/*), not /apis* — without this,
      # aws_apigatewayv2_api with a `tags` block fails on CreateApi.
      "arn:aws:apigateway:*::/tags/*",
    ]
  }

  statement {
    sid    = "ManageCloudFront"
    effect = "Allow"
    actions = [
      "cloudfront:CreateDistribution",
      "cloudfront:GetDistribution",
      "cloudfront:UpdateDistribution",
      "cloudfront:DeleteDistribution",
      "cloudfront:TagResource",
      "cloudfront:UntagResource",
      "cloudfront:ListTagsForResource",
      "cloudfront:CreateOriginAccessControl",
      "cloudfront:GetOriginAccessControl",
      "cloudfront:UpdateOriginAccessControl",
      "cloudfront:DeleteOriginAccessControl",
      "cloudfront:CreateInvalidation",
      "cloudfront:GetInvalidation",
    ]
    resources = ["*"]
  }

  # Custom domain: Route 53 zone + records, and the us-east-1 ACM cert CloudFront
  # requires. Not scoped further: the zone ID does not exist before first apply.
  statement {
    sid    = "ManageCustomDomain"
    effect = "Allow"
    actions = [
      "route53:CreateHostedZone",
      "route53:DeleteHostedZone",
      "route53:GetHostedZone",
      "route53:ListHostedZones",
      "route53:ListHostedZonesByName",
      "route53:ListResourceRecordSets",
      "route53:ChangeResourceRecordSets",
      "route53:GetChange",
      "route53:ChangeTagsForResource",
      "route53:ListTagsForResource",
      "acm:RequestCertificate",
      "acm:DescribeCertificate",
      "acm:DeleteCertificate",
      "acm:ListCertificates",
      "acm:AddTagsToCertificate",
      "acm:RemoveTagsFromCertificate",
      "acm:ListTagsForCertificate",
    ]
    resources = ["*"]
  }

  # IAM roles/policies for the app's own execution roles — both naming
  # conventions in use (alibi-*, Alibi*) across modules.
  statement {
    sid    = "ManageAppRoles"
    effect = "Allow"
    actions = [
      "iam:GetRole",
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:PutRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:GetRolePolicy",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
      "iam:CreateInstanceProfile",
      "iam:DeleteInstanceProfile",
      "iam:AddRoleToInstanceProfile",
      "iam:RemoveRoleFromInstanceProfile",
      "iam:GetInstanceProfile",
    ]
    resources = [
      "arn:aws:iam::*:role/alibi-*",
      "arn:aws:iam::*:role/Alibi*",
      "arn:aws:iam::*:instance-profile/alibi-*",
      "arn:aws:iam::*:instance-profile/Alibi*",
    ]
  }

  statement {
    sid    = "PassAppRoles"
    effect = "Allow"
    actions = [
      "iam:PassRole",
    ]
    resources = [
      "arn:aws:iam::*:role/alibi-*",
      "arn:aws:iam::*:role/Alibi*",
    ]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["lambda.amazonaws.com", "sagemaker.amazonaws.com", "ec2.amazonaws.com"]
    }
  }

  statement {
    sid    = "SageMakerEndpointLifecycle"
    effect = "Allow"
    actions = [
      "sagemaker:CreateModel",
      "sagemaker:DeleteModel",
      "sagemaker:DescribeModel",
      "sagemaker:CreateEndpointConfig",
      "sagemaker:DeleteEndpointConfig",
      "sagemaker:DescribeEndpointConfig",
      "sagemaker:CreateEndpoint",
      "sagemaker:DeleteEndpoint",
      "sagemaker:DescribeEndpoint",
      "sagemaker:UpdateEndpoint",
      "sagemaker:InvokeEndpoint", # ml-endpoint.yml sends one test question
      "sagemaker:AddTags",
      "sagemaker:ListTags",
    ]
    resources = [
      # trailing * : model names carry the served run, e.g. model-a-dev-smoke-002
      "arn:aws:sagemaker:*:*:model/*-${var.environment}*",
      "arn:aws:sagemaker:*:*:endpoint-config/*-${var.environment}*",
      "arn:aws:sagemaker:*:*:endpoint/*-${var.environment}",
    ]
  }

  # SageMaker Training Job control plane — ML/terraform/modules/training
  # itself never calls this (it only writes a local_file job config + launch
  # script, same as training-ec2-gpu/training-external-gpu); this is for
  # ml-deploy.yml to run the generated `aws sagemaker create-training-job`
  # step directly instead of a human running it from their own terminal.
  # The job's own execution role (AlibiSageMakerRole, PassAppRoles above)
  # is what actually pulls the training image and reads/writes S3 — this
  # role only needs to be allowed to submit/monitor the job itself.
  statement {
    sid    = "ManageTrainingJobs"
    effect = "Allow"
    actions = [
      "sagemaker:CreateTrainingJob",
      "sagemaker:DescribeTrainingJob",
      "sagemaker:StopTrainingJob",
      "sagemaker:AddTags",
      "sagemaker:ListTags",
    ]
    resources = ["arn:aws:sagemaker:*:*:training-job/*-${var.environment}-*"]
  }

  statement {
    sid    = "AppAutoScaling"
    effect = "Allow"
    actions = [
      "application-autoscaling:RegisterScalableTarget",
      "application-autoscaling:DeregisterScalableTarget",
      "application-autoscaling:DescribeScalableTargets",
      "application-autoscaling:PutScalingPolicy",
      "application-autoscaling:DeleteScalingPolicy",
      "application-autoscaling:DescribeScalingPolicies",
    ]
    resources = ["*"]
  }

  # The overflow policy below (aws_iam_policy.ci_extra) is a customer-managed
  # policy, so this role has to be able to read and version it itself: every
  # infra-deploy.yml run refreshes module.ci_deploy_role. Scoped to alibi-*
  # policies, and no wider than what PutRolePolicy/AttachRolePolicy on
  # alibi-* roles above already allows.
  statement {
    sid    = "ManageOwnManagedPolicies"
    effect = "Allow"
    actions = [
      "iam:CreatePolicy",
      "iam:DeletePolicy",
      "iam:GetPolicy",
      "iam:GetPolicyVersion",
      "iam:ListPolicyVersions",
      "iam:CreatePolicyVersion",
      "iam:DeletePolicyVersion",
      "iam:ListPolicyTags",
      "iam:TagPolicy",
      "iam:UntagPolicy",
    ]
    resources = ["arn:aws:iam::*:policy/alibi-*"]
  }
}

# An IAM role's inline policies share a 10,240-byte aggregate limit
# (PutRolePolicy 409 LimitExceeded once the internet-gateway statement pushed
# it over). Customer-managed policies get 6,144 bytes EACH and up to 10 can be
# attached, so everything from the training-launch statement onward lives here.
# Keep each half comfortably under its limit; a third policy is the next step.
data "aws_iam_policy_document" "ci_extra" {
  # terraform/modules/rds-autostop's idle-check schedule. Its Lambda and role
  # are already covered by ManageLambda / ManageAppRoles (alibi-*).
  statement {
    sid    = "ManageDbAutostopRule"
    effect = "Allow"
    actions = [
      "events:PutRule",
      "events:DeleteRule",
      "events:DescribeRule",
      "events:PutTargets",
      "events:RemoveTargets",
      "events:ListTargetsByRule",
      "events:TagResource",
      "events:ListTagsForResource",
    ]
    resources = ["arn:aws:events:*:*:rule/alibi-*"]
  }

  # ListTrainingJobs is a list action: it does not support resource-level
  # scoping, so "*" is the only valid resource. Read-only; ml-endpoint.yml uses
  # it to show which completed runs can be deployed.
  statement {
    sid       = "ListTrainingJobs"
    effect    = "Allow"
    actions   = ["sagemaker:ListTrainingJobs"]
    resources = ["*"]
  }

  # training-ec2-gpu: launch template + security group only. The instance
  # itself is started later by the user's own script/credentials, not CI.
  statement {
    sid    = "ManageTrainingLaunchInfra"
    effect = "Allow"
    actions = [
      "ec2:CreateLaunchTemplate",
      "ec2:DeleteLaunchTemplate",
      "ec2:DescribeLaunchTemplates",
      "ec2:DescribeLaunchTemplateVersions",
      "ec2:CreateSecurityGroup",
      "ec2:DeleteSecurityGroup",
      "ec2:DescribeSecurityGroups",
      "ec2:AuthorizeSecurityGroupEgress",
      "ec2:AuthorizeSecurityGroupIngress",
      "ec2:RevokeSecurityGroupEgress",
      "ec2:RevokeSecurityGroupIngress",
      "ec2:DescribeImages",
      "ec2:CreateTags",
      "ec2:DescribeTags",
    ]
    resources = ["*"]
  }

  # Plain RDS instance/subnet-group lifecycle for terraform/envs/database.
  # Scoped to the alibi-* naming convention for the mutating actions;
  # Describe* actions need resources = "*" since RDS doesn't support
  # resource-level permissions on list/describe calls.
  statement {
    sid    = "ManageDatabaseResources"
    effect = "Allow"
    actions = [
      "rds:CreateDBInstance",
      "rds:DeleteDBInstance",
      "rds:ModifyDBInstance",
      "rds:AddTagsToResource",
      "rds:RemoveTagsFromResource",
      "rds:ListTagsForResource",
      "rds:CreateDBSubnetGroup",
      "rds:DeleteDBSubnetGroup",
      "rds:ModifyDBSubnetGroup",
    ]
    resources = [
      "arn:aws:rds:*:*:db:alibi-*",
      "arn:aws:rds:*:*:subgrp:alibi-*",
    ]
  }

  statement {
    sid    = "DescribeDatabase"
    effect = "Allow"
    actions = [
      "rds:DescribeDBInstances",
      "rds:DescribeDBSubnetGroups",
      "rds:DescribeDBEngineVersions",
      "rds:DescribeOrderableDBInstanceOptions",
    ]
    resources = ["*"]
  }

  # The RDS-managed master password (manage_master_user_password = true)
  # lives in a secret AWS names rds!db-<id> for a plain instance (rds!cluster-<id>
  # is the Aurora naming, not used here) — not known before the instance
  # exists, so this is a naming-convention match, not a specific ARN.
  statement {
    sid    = "ManageDatabaseSecret"
    effect = "Allow"
    actions = [
      "secretsmanager:DescribeSecret",
      "secretsmanager:GetSecretValue",
      "secretsmanager:TagResource",
      "secretsmanager:UntagResource",
    ]
    resources = ["arn:aws:secretsmanager:*:*:secret:rds!db-*"]
  }

  # aws_vpc/aws_subnets data sources in terraform/modules/rds-postgres
  # (default VPC lookup for the instance's subnet group). DescribeVpcAttribute
  # is needed too — the aws_vpc data source reads enableDnsHostnames/
  # enableDnsSupport via that separate call, not DescribeVpcs.
  statement {
    sid    = "DescribeVpcNetwork"
    effect = "Allow"
    actions = [
      "ec2:DescribeVpcs",
      "ec2:DescribeVpcAttribute",
      "ec2:DescribeSubnets",
      "ec2:DescribeAvailabilityZones",
    ]
    resources = ["*"]
  }

  # SSM bastion (terraform/modules/bastion, composed into
  # terraform/envs/database) — the EC2 jump host that gives developers
  # SSM port-forwarding access to the private RDS instance. EC2 actions
  # can't be resource-scoped meaningfully for RunInstances, so they're "*";
  # the IAM role/instance-profile lifecycle below is scoped to the
  # alibi-bastion-* naming convention.
  statement {
    sid    = "ManageBastionInstance"
    effect = "Allow"
    actions = [
      "ec2:RunInstances",
      "ec2:TerminateInstances",
      "ec2:StartInstances",
      "ec2:StopInstances",
      "ec2:DescribeInstances",
      "ec2:DescribeInstanceAttribute",
      "ec2:DescribeInstanceStatus",
      "ec2:DescribeInstanceCreditSpecifications",
      "ec2:DescribeInstanceTypes",
      "ec2:DescribeImages",
      "ec2:DescribeVolumes",
      "ec2:DescribeNetworkInterfaces",
      "ec2:DescribeIamInstanceProfileAssociations",
      "ec2:AssociateIamInstanceProfile",
      "ec2:CreateSecurityGroup",
      "ec2:DeleteSecurityGroup",
      "ec2:DescribeSecurityGroups",
      "ec2:DescribeSecurityGroupRules",
      "ec2:AuthorizeSecurityGroupEgress",
      "ec2:RevokeSecurityGroupEgress",
      "ec2:ModifyInstanceMetadataOptions",
      "ec2:CreateTags",
      "ec2:DeleteTags",
      "ec2:DescribeTags",
    ]
    resources = ["*"]
  }

  # The default VPC's internet gateway attachment, managed by
  # terraform/modules/bastion (aws_internet_gateway_attachment): the bastion's
  # only route to the SSM endpoints. Describe* has no resource-level scoping.
  statement {
    sid    = "ManageDefaultVpcInternetGatewayAttachment"
    effect = "Allow"
    actions = [
      "ec2:AttachInternetGateway",
      "ec2:DetachInternetGateway",
      "ec2:DescribeInternetGateways",
    ]
    resources = ["*"]
  }

  # The bastion's instance role + instance profile (SSM core only). Scoped
  # to alibi-bastion-*; PassRole is what lets RunInstances launch the
  # instance with this profile.
  statement {
    sid    = "ManageBastionRole"
    effect = "Allow"
    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:GetRole",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:ListAttachedRolePolicies",
      "iam:ListRolePolicies",
      "iam:ListInstanceProfilesForRole",
      "iam:CreateInstanceProfile",
      "iam:DeleteInstanceProfile",
      "iam:GetInstanceProfile",
      "iam:TagInstanceProfile",
      "iam:AddRoleToInstanceProfile",
      "iam:RemoveRoleFromInstanceProfile",
      "iam:PassRole",
    ]
    resources = [
      "arn:aws:iam::*:role/alibi-bastion-*",
      "arn:aws:iam::*:instance-profile/alibi-bastion-*",
    ]
  }

  # Read the Amazon Linux 2023 AMI id from the public SSM parameter path
  # (data.aws_ssm_parameter.al2023 in terraform/modules/bastion).
  statement {
    sid       = "ReadPublicAmiParameter"
    effect    = "Allow"
    actions   = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = ["arn:aws:ssm:*::parameter/aws/service/ami-amazon-linux-latest/*"]
  }

  # ── Module B ingestion (backend/terraform/modules/ingest-pipeline) ───────

  statement {
    sid    = "ManageIngestQueues"
    effect = "Allow"
    actions = [
      "sqs:CreateQueue",
      "sqs:DeleteQueue",
      "sqs:GetQueueAttributes",
      "sqs:SetQueueAttributes",
      "sqs:GetQueueUrl",
      "sqs:ListQueueTags",
      "sqs:TagQueue",
      "sqs:UntagQueue",
    ]
    resources = ["arn:aws:sqs:*:*:alibi-*"]
  }

  # The processor's container image. Module B's stage 1 is the only container
  # left — the API went back to a zip when LiteLLM was removed. ECR's auth token call is account-wide by
  # design (GetAuthorizationToken takes no resource), so it needs "*"; every
  # repository-scoped action below is restricted to alibi-*.
  statement {
    sid       = "EcrAuth"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "ManageIngestImages"
    effect = "Allow"
    actions = [
      "ecr:CreateRepository",
      "ecr:DeleteRepository",
      "ecr:DescribeRepositories",
      "ecr:ListTagsForResource",
      "ecr:TagResource",
      "ecr:UntagResource",
      "ecr:PutImageScanningConfiguration",
      "ecr:PutImageTagMutability",
      "ecr:GetLifecyclePolicy",
      "ecr:PutLifecyclePolicy",
      "ecr:DeleteLifecyclePolicy",
      "ecr:GetRepositoryPolicy",
      "ecr:SetRepositoryPolicy",
      # The image push itself, from backend-deploy.yml.
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
      "ecr:DescribeImages",
    ]
    resources = ["arn:aws:ecr:*:*:repository/alibi-*"]
  }

  # SQS → Lambda wiring. Event-source mappings are identified by a UUID that
  # doesn't exist before creation, so these cannot be resource-scoped; the
  # Lambda side is already restricted to alibi-* by ManageLambda above.
  statement {
    sid    = "ManageEventSourceMappings"
    effect = "Allow"
    actions = [
      "lambda:CreateEventSourceMapping",
      "lambda:DeleteEventSourceMapping",
      "lambda:GetEventSourceMapping",
      "lambda:UpdateEventSourceMapping",
      "lambda:ListEventSourceMappings",
      # The provider reads tags on every refresh of an
      # aws_lambda_event_source_mapping — ManageLambda's ListTags above
      # doesn't cover it, since it's scoped to function:alibi-* ARNs and an
      # event-source-mapping ARN never matches that pattern.
      "lambda:ListTags",
    ]
    resources = ["*"]
  }

  # The S3 Gateway VPC Endpoint — free, and the only way an in-VPC Lambda
  # reaches S3 at all (there is no NAT). Endpoint ids aren't known in advance,
  # and route-table lookups don't support resource-level permissions.
  statement {
    sid    = "ManageS3GatewayEndpoint"
    effect = "Allow"
    actions = [
      "ec2:CreateVpcEndpoint",
      "ec2:DeleteVpcEndpoints",
      "ec2:ModifyVpcEndpoint",
      "ec2:DescribeVpcEndpoints",
      "ec2:DescribeRouteTables",
      "ec2:DescribePrefixLists",
      "ec2:DescribeVpcEndpointServices",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "ManageBudgetAndAlerts"
    effect = "Allow"
    actions = [
      "budgets:ViewBudget",
      "budgets:ModifyBudget",
      "budgets:ListTagsForResource",
    ]
    resources = ["arn:aws:budgets::*:budget/alibi-*"]
  }
}

resource "aws_iam_role_policy" "ci" {
  name   = "alibi-ci-deploy-${var.environment}"
  role   = aws_iam_role.ci.id
  policy = data.aws_iam_policy_document.ci.json
}

resource "aws_iam_policy" "ci_extra" {
  name   = "alibi-ci-deploy-${var.environment}-extra"
  policy = data.aws_iam_policy_document.ci_extra.json
  tags   = { Project = "alibi", Environment = var.environment, Purpose = "unified-ci-deploy" }
}

resource "aws_iam_role_policy_attachment" "ci_extra" {
  role       = aws_iam_role.ci.name
  policy_arn = aws_iam_policy.ci_extra.arn
}

# The fine-tuned models' endpoint as ML/terraform/modules/inference builds it
# since 2026-10-12: an inference component (created through the Cloud Control
# API by the awscc provider), Application Auto Scaling on its copy count, and
# the alarm that wakes it. A third policy rather than more statements in the
# second: each customer-managed policy is capped at 6,144 bytes.
data "aws_iam_policy_document" "ci_models" {
  statement {
    sid    = "ManageModelsInferenceComponent"
    effect = "Allow"
    actions = [
      "sagemaker:CreateInferenceComponent",
      "sagemaker:DescribeInferenceComponent",
      "sagemaker:UpdateInferenceComponent",
      "sagemaker:DeleteInferenceComponent",
      "sagemaker:InvokeEndpoint", # ml-deploy.yml's test question names the IC
      "sagemaker:AddTags",
      "sagemaker:DeleteTags",
      "sagemaker:ListTags",
    ]
    resources = [
      "arn:aws:sagemaker:*:*:inference-component/*-${var.environment}-*",
      "arn:aws:sagemaker:*:*:endpoint/*-${var.environment}",
    ]
  }

  # The awscc provider drives every resource through Cloud Control; these are
  # its own API actions (namespaced under cloudformation), which do not support
  # resource-level scoping. What it may actually create is bounded by the
  # SageMaker statement above.
  statement {
    sid    = "CloudControlApi"
    effect = "Allow"
    actions = [
      "cloudformation:CreateResource",
      "cloudformation:GetResource",
      "cloudformation:UpdateResource",
      "cloudformation:DeleteResource",
      "cloudformation:GetResourceRequestStatus",
      "cloudformation:ListResources",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "ManageWakeAlarm"
    effect = "Allow"
    actions = [
      "cloudwatch:ListTagsForResource",
      "cloudwatch:TagResource",
      "cloudwatch:UntagResource",
    ]
    resources = ["arn:aws:cloudwatch:*:*:alarm:*-${var.environment}-*"]
  }

  # Application Auto Scaling does not just use these on the caller's behalf: on
  # RegisterScalableTarget it checks that the CALLER holds them (the first
  # apply failed with "User is missing the following permissions: ..."), and
  # that check ignores resource scoping. DescribeAlarms cannot be scoped at all.
  # Alarms are still only created by the policies Terraform declares.
  statement {
    sid    = "AutoScalingCallerChecks"
    effect = "Allow"
    actions = [
      "cloudwatch:PutMetricAlarm",
      "cloudwatch:DeleteAlarms",
      "cloudwatch:DescribeAlarms",
      "sagemaker:UpdateInferenceComponentRuntimeConfig",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "AppAutoScalingTags"
    effect = "Allow"
    actions = [
      "application-autoscaling:ListTagsForResource",
      "application-autoscaling:TagResource",
      "application-autoscaling:UntagResource",
    ]
    resources = ["*"]
  }

  # Application Auto Scaling creates this role the first time anything
  # registers a SageMaker scalable target in the account — which nothing had,
  # so the first apply has to be allowed to create it. Only this one role.
  statement {
    sid       = "CreateSageMakerAutoScalingRole"
    effect    = "Allow"
    actions   = ["iam:CreateServiceLinkedRole"]
    resources = ["arn:aws:iam::*:role/aws-service-role/sagemaker.application-autoscaling.amazonaws.com/AWSServiceRoleForApplicationAutoScaling_SageMakerEndpoint"]
    condition {
      test     = "StringEquals"
      variable = "iam:AWSServiceName"
      values   = ["sagemaker.application-autoscaling.amazonaws.com"]
    }
  }
}

resource "aws_iam_policy" "ci_models" {
  name   = "alibi-ci-deploy-${var.environment}-models"
  policy = data.aws_iam_policy_document.ci_models.json
  tags   = { Project = "alibi", Environment = var.environment, Purpose = "unified-ci-deploy" }
}

resource "aws_iam_role_policy_attachment" "ci_models" {
  role       = aws_iam_role.ci.name
  policy_arn = aws_iam_policy.ci_models.arn
}
