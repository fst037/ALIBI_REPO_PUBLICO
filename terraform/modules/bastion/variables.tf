variable "environment" {
  type = string
}

# t4g.nano (Graviton, arm64) is the cheapest EC2 type there is (~$3/mo
# running 24/7, vs ~$7.6 for t3.micro). The bastion only forwards a TCP port,
# so 0.5 GB RAM is plenty. Must stay a Graviton (t4g) type — main.tf resolves
# the arm64 AL2023 AMI; an x86 type would need the x86_64 AMI path back.
variable "instance_type" {
  type    = string
  default = "t4g.nano"
}

# Minutes with no open tunnel before the bastion powers itself off (see the
# user_data in main.tf). Restart it with the "ALIBI: Bastion start" task.
variable "idle_shutdown_minutes" {
  type    = number
  default = 60
}

# The default VPC's internet gateway. It exists in every default VPC but was
# found DETACHED on 2026-09-28 (default route state "blackhole"), which left
# the bastion with no path to the SSM endpoints. Managed here so a detach
# shows up as drift on the next apply instead of a silent TargetNotConnected.
variable "internet_gateway_id" {
  type    = string
  default = "igw-0487b8b4a49249c42"
}
