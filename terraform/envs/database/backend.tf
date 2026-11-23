# Separate state from terraform/envs/dev's unified root (key
# "alibi/unified/dev/...") — this root is applied independently and never
# as part of the unified deploy-all/destroy-all cycle, since it holds real
# data. Same state bucket, different key.
terraform {
  backend "s3" {
    bucket = "alibi-terraform-state-213598695817"
    key    = "alibi/database/dev/terraform.tfstate"
    region = "us-east-1"
  }
}
