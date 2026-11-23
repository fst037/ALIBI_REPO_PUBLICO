variable "environment" {
  type = string
}

variable "endpoint_name" {
  type = string
}

variable "inference_component_name" {
  type = string
}

# Auto scaling scales in after ~15 idle minutes; this is only a backstop, so it
# waits longer and never races the normal path.
variable "idle_minutes" {
  type    = number
  default = 25
}

# Idle this long with an instance still running means scaling is not going to
# finish by itself.
variable "hard_idle_minutes" {
  type    = number
  default = 60
}

variable "check_rate_minutes" {
  type    = number
  default = 5
}

variable "delete_stuck_endpoint" {
  description = "Delete the endpoint when it is still running an instance after hard_idle_minutes with no use. Re-creating it is one ml-deploy.yml run."
  type        = bool
  default     = true
}
