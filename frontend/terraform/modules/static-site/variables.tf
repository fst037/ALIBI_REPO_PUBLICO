variable "environment" {
  type = string
}

variable "price_class" {
  description = "CloudFront price class. PriceClass_100 = US/Canada/Europe only (cheapest)."
  type        = string
  default     = "PriceClass_100"
}

# API Gateway domain (host only, no scheme) to route `/api/v1/*` to as a second
# CloudFront origin — so the SPA calls the backend same-origin (no CORS) via its
# relative `/api/v1` URLs. Left empty, no API origin/behavior is added.
variable "api_gateway_domain" {
  type    = string
  default = ""
}

# Custom domain. Set together: CloudFront rejects aliases without a certificate
# that covers them. The cert must live in us-east-1.
variable "aliases" {
  type    = list(string)
  default = []
}

variable "certificate_arn" {
  type    = string
  default = ""
}
