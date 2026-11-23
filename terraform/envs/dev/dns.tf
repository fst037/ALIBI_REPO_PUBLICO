# Custom domain for the SPA. One domain is enough: the SPA reaches the API
# same-origin through CloudFront (/api/v1/*), so API Gateway needs no domain.
#
# Two phases because ACM's DNS validation cannot succeed until nic.ar delegates
# the zone to Route 53 — see var.domain_delegated.
#
# Deliberately NOT in infra-destroy.yml's -target list: recreating the zone
# would change its name servers and force a new delegation at nic.ar.

locals {
  zone_enabled   = var.domain_name != ""
  domain_enabled = local.zone_enabled && var.domain_delegated
  domain_names   = concat([var.domain_name], var.domain_include_www ? ["www.${var.domain_name}"] : [])

  custom_domain_aliases  = local.domain_enabled ? local.domain_names : []
  custom_domain_cert_arn = local.domain_enabled ? aws_acm_certificate_validation.main[0].certificate_arn : ""
}

resource "aws_route53_zone" "main" {
  count = local.zone_enabled ? 1 : 0
  name  = var.domain_name
  tags  = { Project = "alibi", Environment = var.environment }
}

# CloudFront only reads certificates from us-east-1; aws_region is us-east-1, so
# the default provider is the right one. Revisit if aws_region ever changes.
resource "aws_acm_certificate" "main" {
  count                     = local.domain_enabled ? 1 : 0
  domain_name               = var.domain_name
  subject_alternative_names = slice(local.domain_names, 1, length(local.domain_names))
  validation_method         = "DNS"
  tags                      = { Project = "alibi", Environment = var.environment }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "cert_validation" {
  for_each = local.domain_enabled ? {
    for o in aws_acm_certificate.main[0].domain_validation_options : o.domain_name => o
  } : {}

  zone_id         = aws_route53_zone.main[0].zone_id
  name            = each.value.resource_record_name
  type            = each.value.resource_record_type
  records         = [each.value.resource_record_value]
  ttl             = 60
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "main" {
  count                   = local.domain_enabled ? 1 : 0
  certificate_arn         = aws_acm_certificate.main[0].arn
  validation_record_fqdns = [for r in aws_route53_record.cert_validation : r.fqdn]
}

resource "aws_route53_record" "site" {
  for_each = local.domain_enabled ? {
    for pair in setproduct(local.domain_names, ["A", "AAAA"]) : "${pair[0]}-${pair[1]}" => {
      name = pair[0]
      type = pair[1]
    }
  } : {}

  zone_id = aws_route53_zone.main[0].zone_id
  name    = each.value.name
  type    = each.value.type

  alias {
    name                   = module.frontend.distribution_domain_name
    zone_id                = module.frontend.distribution_hosted_zone_id
    evaluate_target_health = false
  }
}
