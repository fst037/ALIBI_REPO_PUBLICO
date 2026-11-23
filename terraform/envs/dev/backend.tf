# Same Terraform state bucket as before (backend/frontend/ML each had their
# own key here); this root uses one new key so all three live in a single
# state.
# No `profile`: this root runs in GitHub Actions (OIDC-supplied credentials,
# no local AWS CLI profile on the runner) as well as from your own terminal.
# For local runs, set AWS_PROFILE=alibi in your shell instead — see versions.tf.
terraform {
  backend "s3" {
    bucket = "alibi-terraform-state-213598695817"
    key    = "alibi/unified/dev/terraform.tfstate"
    region = "us-east-1"
  }
}
