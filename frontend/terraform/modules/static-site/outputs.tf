output "bucket_name" {
  value = aws_s3_bucket.site.bucket
}

output "bucket_arn" {
  value = aws_s3_bucket.site.arn
}

output "distribution_id" {
  value = aws_cloudfront_distribution.site.id
}

output "distribution_arn" {
  value = aws_cloudfront_distribution.site.arn
}

output "distribution_domain_name" {
  value = aws_cloudfront_distribution.site.domain_name
}

# Fixed by AWS for every distribution — needed for Route 53 aliases.
output "distribution_hosted_zone_id" {
  value = aws_cloudfront_distribution.site.hosted_zone_id
}
