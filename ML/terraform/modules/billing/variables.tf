variable "environment" {
  type = string
}

variable "alert_email" {
  description = "Email address for budget alert notifications"
  type        = string
}

variable "monthly_limit_usd" {
  description = "Monthly budget limit in USD. Alert fires at 80% actual and 100% forecasted."
  type        = number
  default     = 200
}
