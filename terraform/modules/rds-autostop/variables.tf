variable "environment" {
  type = string
}

variable "db_instance_identifier" {
  type = string
}

variable "db_instance_arn" {
  type = string
}

# Same window as the bastion's idle_shutdown_minutes. Starting a stopped RDS
# takes ~5-10 minutes, so too short a window makes that wait frequent.
variable "idle_minutes" {
  type    = number
  default = 60
}

# DatabaseConnections at or below this counts as idle. 0 assumes RDS's own
# internal sessions are not counted in the metric; if CloudWatch shows a
# non-zero floor while nobody is connected, raise this to that floor. Too low
# fails safe: the instance just never stops, which is today's cost, not an
# outage.
variable "idle_max_connections" {
  type    = number
  default = 0
}

# Lambdas whose invocations count as database activity. Names, not module
# outputs: these live in the unified root (terraform/envs/dev), which this
# root deliberately shares no state with.
variable "activity_function_names" {
  type = list(string)
}

# A running bastion counts as activity (a developer has a tunnel open, or had
# one within its own idle window). Empty disables the check.
variable "bastion_instance_id" {
  type    = string
  default = ""
}

# How often the idle check runs. Worst case the instance stops this long after
# the idle window ends.
variable "check_rate_minutes" {
  type    = number
  default = 10
}
