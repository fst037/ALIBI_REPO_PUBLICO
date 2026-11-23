# ingest-pipeline — deploy runbook

Module B's data plane (S3) and its two-stage worker. Read
`main.tf`'s header for *why* it is shaped this way; this file is *how to
deploy it*.

> **Every `terraform` and `aws` command below must be run from your own
> terminal.** The Zscaler proxy breaks provider mTLS in agent subprocesses —
> `terraform fmt` works, `terraform validate` and `apply` do not. Day-to-day
> deploys go through GitHub Actions instead, which runs on GitHub's runners.

---

## The one awkward part: a chicken-and-egg on the first apply

Lambda validates a container image **at CreateFunction time**. The processor's
image lives in an ECR repository that this module also creates — so on the very
first apply the function cannot be created, because the image it points at does
not exist yet, because the repository it would live in does not exist yet.

There is no way to express that ordering in a single `terraform apply` — but
`.github/workflows/infra-deploy.yml`'s "Bootstrap the ingest processor image"
step now does this automatically: it targets the ECR repository alone, checks
whether it already holds an image (via `aws ecr describe-images`, not
Terraform state, so it recovers even if the repo were ever destroyed and
recreated), and pushes a bootstrap image with GitHub-hosted Docker if not.
This runs on every `infra-deploy.yml` invocation and is a no-op once a real
image exists, so it costs nothing to leave in place.

Set `ingest_enabled = true` (and `allowed_origins`, once the CloudFront URL is
known) in `terraform/envs/dev/terraform.tfvars` — **committed**, not passed as
a one-off `-var` — since `infra-deploy.yml` runs a plain `terraform apply`
with no flags of its own and reads whatever `terraform.tfvars` says. That file
is the only thing that needs to change to turn Module B on; everything else in
this section runs unattended in CI from then on.

### If you're doing this from your own terminal instead (no CI available)

The three steps CI now automates, for when you're offline or the Actions
runner path is broken:

```powershell
# 1. Create the repository only.
cd terraform/envs/dev
terraform apply -target='module.ingest[0].aws_ecr_repository.processor' -var='ingest_enabled=true'

# 2. Push a bootstrap image (needs local Docker).
$Account  = (aws sts get-caller-identity --query Account --output text --no-verify-ssl)
$Registry = "$Account.dkr.ecr.us-east-1.amazonaws.com"
$Repo     = "$Registry/alibi-ingest-processor-dev"
aws ecr get-login-password --region us-east-1 --no-verify-ssl | docker login --username AWS --password-stdin $Registry
cd ../../../backend
docker build -f Dockerfile.processor -t "${Repo}:bootstrap" .
docker push "${Repo}:bootstrap"

# 3. Apply everything.
cd ../terraform/envs/dev
terraform apply -var='ingest_enabled=true'
```

### After that

`.github/workflows/backend-deploy.yml` builds and pushes a new image tagged
with the commit SHA on every push to `backend/**`, then updates the function.
Terraform ignores `image_uri` so it will not revert what CI deployed.

---

## Checking it actually works

```powershell
# 1. Nothing should ever be in the DLQ. Anything here is a bug report.
aws sqs get-queue-attributes --queue-url (terraform output -raw ingest_dlq_url) `
  --attribute-names ApproximateNumberOfMessages --no-verify-ssl

# 2. The processor's logs — the fastest way to see a real upload flow through.
aws logs tail /aws/lambda/alibi-ingest-processor-dev --follow --no-verify-ssl

# 3. The committer's. "stage 2 done: committed=True" is the success line.
aws logs tail /aws/lambda/alibi-ingest-committer-dev --follow --no-verify-ssl
```

Then upload a file through the real UI. The whole chain is:

```
browser PUT → S3 → SQS → processor (writes Parquet, deletes the raw file)
                       → committer (writes Postgres) → GET /uploads/{id} says ready
```

## When something goes wrong, in likelihood order

| Symptom | Cause |
|---|---|
| Upload 403s in the browser at the PUT | The bucket's CORS `allowed_origins` doesn't include the CloudFront URL. Set `allowed_origins` and re-apply — a presigned URL is not enough on its own. |
| Upload stays `processing` forever | The processor never ran. Check the SQS queue depth and the bucket notification: the `uploads/` prefix filter must match the key the API presigned. |
| Processor logs success, upload still `processing` | Stage 2 never ran or failed. Check `ALIBI_COMMITTER_FUNCTION_NAME` on the processor, and the committer's own logs for a database connection error. |
| Committer times out with no error | It cannot reach RDS. Its security group needs egress on 5432 and it must be in the same VPC — that is `vpc_config` plus `aws_security_group.committer`. |
| `GET /datasets/{id}/preview` 500s | The API Lambda cannot reach S3. It is in the VPC, so this is the S3 Gateway VPC Endpoint missing — check `create_s3_endpoint`. |
| The same file is processed twice | SQS visibility timeout is below the processor's timeout. They are wired to the same variable for exactly this reason; check nothing overrode one of them. |

## What this module deliberately does not do

- **No NAT gateway.** That is the whole point of the two-stage split (~$32/mo saved).
- **No interface VPC endpoints.** The gateway endpoint for S3 is free; interface endpoints are ~$7-15/mo each.
- **No DLQ alarm.** Worth adding when this stops being a thesis project.
- **No per-user IAM.** Isolation is `owner_id` scoping in the backend plus a per-user key prefix, not a policy per user.
