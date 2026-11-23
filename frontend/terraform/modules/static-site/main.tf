# Static hosting for the ALIBI React SPA: private S3 bucket + CloudFront
# with Origin Access Control (OAC). No public S3 access — CloudFront is the
# only reader, using the modern OAC mechanism (not the deprecated OAI).

data "aws_caller_identity" "current" {}

locals {
  account_suffix = data.aws_caller_identity.current.account_id
  bucket_name    = "alibi-frontend-${var.environment}-${local.account_suffix}"
}

resource "aws_s3_bucket" "site" {
  bucket = local.bucket_name
  tags   = { Project = "alibi", Environment = var.environment, Purpose = "frontend-static-site" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "site" {
  bucket = aws_s3_bucket.site.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "site" {
  bucket = aws_s3_bucket.site.id
  versioning_configuration { status = "Enabled" }
}

# ── CloudFront ────────────────────────────────────────────────────────────────

resource "aws_cloudfront_origin_access_control" "site" {
  name                              = "alibi-frontend-${var.environment}"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "site" {
  enabled             = true
  default_root_object = "index.html"
  price_class         = var.price_class
  comment             = "ALIBI frontend (${var.environment})"
  aliases             = var.aliases

  origin {
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_id                = "s3-${local.bucket_name}"
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  # Backend API as a second origin, so the SPA reaches it same-origin. Only
  # added when api_gateway_domain is set.
  dynamic "origin" {
    for_each = var.api_gateway_domain != "" ? [1] : []
    content {
      domain_name = var.api_gateway_domain
      origin_id   = "apigw"
      custom_origin_config {
        http_port              = 80
        https_port             = 443
        origin_protocol_policy = "https-only"
        origin_ssl_protocols   = ["TLSv1.2"]
      }
    }
  }

  default_cache_behavior {
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "s3-${local.bucket_name}"
    viewer_protocol_policy = "redirect-to-https"
    compress               = true

    forwarded_values {
      query_string = false
      cookies { forward = "none" }
    }
  }

  # Route `/api/v1/*` to the backend (no caching; forward the bearer token +
  # content type + query strings). Same-origin, so the SPA needs no CORS and
  # no code change (its generated URLs are already relative `/api/v1/...`).
  dynamic "ordered_cache_behavior" {
    for_each = var.api_gateway_domain != "" ? [1] : []
    content {
      path_pattern           = "/api/v1/*"
      target_origin_id       = "apigw"
      viewer_protocol_policy = "redirect-to-https"
      allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
      cached_methods         = ["GET", "HEAD"]
      compress               = true

      forwarded_values {
        query_string = true
        headers      = ["Authorization", "Content-Type"]
        cookies { forward = "none" }
      }
      min_ttl     = 0
      default_ttl = 0
      max_ttl     = 0
    }
  }

  # React Router client-side routing: a deep link to a route (a key the bucket
  # doesn't have) returns 403 under OAC + a GetObject-only policy (S3 returns
  # 403, not 404, without ListBucket), which falls back to index.html so the
  # SPA router takes over. NB: there is deliberately NO 404 fallback — a 404
  # from the API origin must reach the client as-is (the backend uses 404 for
  # not-found), not be rewritten to index.html.
  custom_error_response {
    error_code         = 403
    response_code      = 200
    response_page_path = "/index.html"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  # Default *.cloudfront.net cert until a custom domain's ACM cert is supplied.
  dynamic "viewer_certificate" {
    for_each = var.certificate_arn == "" ? [1] : []
    content {
      cloudfront_default_certificate = true
    }
  }

  dynamic "viewer_certificate" {
    for_each = var.certificate_arn != "" ? [1] : []
    content {
      acm_certificate_arn      = var.certificate_arn
      ssl_support_method       = "sni-only"
      minimum_protocol_version = "TLSv1.2_2021"
    }
  }

  tags = { Project = "alibi", Environment = var.environment, Purpose = "frontend-static-site" }
}

resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowCloudFrontOAC"
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.site.arn}/*"
      Condition = {
        StringEquals = {
          "AWS:SourceArn" = aws_cloudfront_distribution.site.arn
        }
      }
    }]
  })
}
