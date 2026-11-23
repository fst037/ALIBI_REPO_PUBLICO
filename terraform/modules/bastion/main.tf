# SSM bastion — a minimal EC2 jump host whose only job is to let developers
# reach the private RDS instance (terraform/modules/rds-postgres) over an
# AWS Systems Manager port-forwarding session, with NO inbound rules and NO
# IP allowlist to maintain. Chosen over "make RDS public + SG allowlist"
# because two developers work from changing IPs/locations: SSM auth is by
# IAM, so any dev with credentials connects from anywhere without touching a
# security group.
#
# No EC2 Free Tier applies to this account — every hour this runs bills at t4g.nano's standard rate,
# plus the ~$0.005/hr AWS charges per public IPv4 address regardless of
# Free Tier status. Stop it when idle; it has no state to lose. Stopped, it
# costs only its 8 GB volume (~$0.64/mo): the auto-assigned public IP is
# released on stop, so the IPv4 charge stops too.
#
# How a dev connects (needs AWS CLI + the Session Manager plugin locally):
#   aws ssm start-session --target <instance_id> \
#     --document-name AWS-StartPortForwardingSessionToRemoteHost \
#     --parameters host=<rds_host>,portNumber=5432,localPortNumber=5432
#   # then point psql/DBeaver at localhost:5432

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# The bastion's only route to the SSM endpoints is its public IP through the
# default VPC's internet gateway (no NAT, no interface endpoints — see the
# comment on associate_public_ip_address below). The gateway itself is not
# something Terraform created, so this only manages its ATTACHMENT to the VPC:
# if it is ever detached again, the default route goes "blackhole" and the
# SSM agent times out, and the next db-deploy re-attaches it. Destroying this
# resource detaches the gateway; the database root is never destroyed by
# automation, so that does not happen in practice.
resource "aws_internet_gateway_attachment" "default" {
  internet_gateway_id = var.internet_gateway_id
  vpc_id              = data.aws_vpc.default.id
}

# Amazon Linux 2023 ships the SSM agent preinstalled and enabled, so no
# user_data is needed for Session Manager to work. Resolved from the public
# SSM public-parameter path so the AMI id is never hardcoded/stale.
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

# Instance role: only AmazonSSMManagedInstanceCore, the managed policy that
# lets the SSM agent register the instance and service a Session Manager
# connection. No S3/DB/secret access — the bastion only forwards a TCP port,
# it never reads the database itself.
data "aws_iam_policy_document" "assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "bastion" {
  name               = "alibi-bastion-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = { Project = "alibi", Environment = var.environment }
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.bastion.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "bastion" {
  name = "alibi-bastion-${var.environment}"
  role = aws_iam_role.bastion.name
  tags = { Project = "alibi", Environment = var.environment }
}

# No ingress at all — SSM is a fully outbound protocol (the agent dials out
# to the SSM endpoints). Egress on 443 only, which is all the agent needs.
# The bastion reaches RDS on 5432 because it sits inside the default VPC,
# which the RDS security group already admits (its ingress is the whole VPC
# CIDR) — no extra RDS-side rule is required.
resource "aws_security_group" "bastion" {
  name        = "alibi-bastion-${var.environment}"
  description = "SSM bastion - no inbound, outbound 443 for the SSM agent + 5432 to reach RDS."
  vpc_id      = data.aws_vpc.default.id

  egress {
    description = "HTTPS out for the SSM agent (Session Manager endpoints)"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Postgres to the RDS instance inside the VPC"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.default.cidr_block]
  }

  tags = { Project = "alibi", Environment = var.environment }
}

resource "aws_instance" "bastion" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = var.instance_type
  subnet_id              = data.aws_subnets.default.ids[0]
  iam_instance_profile   = aws_iam_instance_profile.bastion.name
  vpc_security_group_ids = [aws_security_group.bastion.id]

  # Public IP so the SSM agent can reach the SSM endpoints via the default
  # VPC's internet gateway — avoids provisioning a NAT gateway (~$32/mo,
  # which costs far more than the public IPv4 it would save) or SSM VPC
  # interface endpoints.
  associate_public_ip_address = true

  # No SSH key pair: access is exclusively through SSM Session Manager, so
  # the instance never needs a key and exposes no SSH port.

  metadata_options {
    http_tokens = "required" # IMDSv2 only
  }

  # A forgotten running bastion bills ~$7/mo for nothing. A systemd timer
  # checks every 5 minutes for an established connection to RDS:5432 (which is
  # what an SSM port-forwarding session is, from the instance's side) and
  # powers the instance off after var.idle_shutdown_minutes without one.
  # An OS poweroff on an EBS-backed instance *stops* it (AWS's default
  # InstanceInitiatedShutdownBehavior), so the instance ID stays valid
  # across restarts. Deliberately not set explicitly: the provider applies it with a
  # post-create ec2:ModifyInstanceAttribute call, which the CI role lacks.
  # /run is wiped on boot, so the idle clock restarts on every start — a fresh
  # start gets the full window to open a tunnel before it can shut down.

  user_data = <<-EOT
    #!/bin/bash
    cat > /usr/local/bin/bastion-idle-check <<'SCRIPT'
    #!/bin/bash
    STATE=/run/bastion-idle-since
    now=$(date +%s)
    if [ -n "$(ss -Htn state established '( dport = :5432 )')" ]; then
      rm -f "$STATE"; exit 0
    fi
    [ -f "$STATE" ] || echo "$now" > "$STATE"
    if [ $((now - $(cat "$STATE"))) -ge ${var.idle_shutdown_minutes * 60} ]; then
      /usr/bin/systemctl poweroff
    fi
    SCRIPT
    chmod +x /usr/local/bin/bastion-idle-check
    cat > /etc/systemd/system/bastion-idle.service <<'UNIT'
    [Service]
    Type=oneshot
    ExecStart=/usr/local/bin/bastion-idle-check
    UNIT
    cat > /etc/systemd/system/bastion-idle.timer <<'UNIT'
    [Timer]
    OnBootSec=5min
    OnUnitActiveSec=5min
    [Install]
    WantedBy=timers.target
    UNIT
    systemctl daemon-reload
    systemctl enable --now bastion-idle.timer
  EOT

  # Smallest root volume AL2023 allows (gp3, 8 GB) — the only cost while the
  # instance is stopped.
  root_block_device {
    volume_type = "gp3"
    volume_size = 8
  }

  # The AMI parameter tracks "latest", so every new AL2023 release would
  # otherwise plan a destroy/recreate of the bastion (new instance ID). Added
  # only after the x86 -> arm64 replacement had applied (2026-09-28): with it
  # present from the start, Terraform skips the AMI swap and fails resizing
  # the existing x86 instance in place. user_data only runs on first boot, so
  # a change to it needs a manual replace (`terraform apply -replace=...`).
  lifecycle {
    ignore_changes = [ami]
  }

  tags = { Name = "alibi-bastion-${var.environment}", Project = "alibi", Environment = var.environment }
}
