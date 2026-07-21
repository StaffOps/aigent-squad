---
name: cloudfront-patterns
description: "Use when configuring CloudFront distributions, cache behaviors, WAF integration, or S3/ALB origins. Covers OAI vs OAC, signed URLs, ACM certificates, <org> Terraform templates, and cache invalidation patterns."
keywords: [cloudfront, s3, terraform, waf, alb, oai, oac, acm]
---
# AWS CloudFront Patterns

CDN and content delivery patterns for <org>'s AWS environments.

## Core concepts

### Distribution anatomy

```
Client → CloudFront Edge → Origin (S3 / ALB / Custom)
                ↓
         Cache behavior (path pattern → origin + cache policy)
```

A distribution has:
- **Origins**: where content comes from (S3, ALB, custom HTTP)
- **Behaviors**: rules mapping URL paths to origins + cache settings
- **Cache policies**: what to cache, how long, what to include in cache key
- **Origin request policies**: what to forward to origin (headers, cookies, query strings)

### Cache key

The cache key determines what makes a request "unique" for caching:
- URL path (always)
- Query strings (configurable)
- Headers (configurable — careful with cardinality)
- Cookies (configurable)

More items in cache key = lower cache hit ratio.

## Origin types

### S3 origin (static content)

```hcl
resource "aws_cloudfront_distribution" "static" {
  origin {
    domain_name              = aws_s3_bucket.static.bucket_regional_domain_name
    origin_id                = "s3-static"
    origin_access_control_id = aws_cloudfront_origin_access_control.oac.id
  }

  default_cache_behavior {
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "s3-static"
    viewer_protocol_policy = "redirect-to-https"
    cache_policy_id        = data.aws_cloudfront_cache_policy.caching_optimized.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    acm_certificate_arn      = var.acm_cert_arn  # MUST be in us-east-1
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }

  tags = {
    Environment = "PRD"
    CostCenter  = "<cost-center>"
    CostScope   = "FRONT-END"
    CostProject = "PORTAL"
    Name        = "DPM-PORTAL-CDN-PRD"
  }
}
```

### ALB origin (dynamic API)

```hcl
resource "aws_cloudfront_distribution" "api" {
  origin {
    domain_name = aws_lb.api.dns_name
    origin_id   = "alb-api"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "alb-api"
    viewer_protocol_policy = "redirect-to-https"
    cache_policy_id        = data.aws_cloudfront_cache_policy.caching_disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer.id
  }

  # ...
}
```

## OAI vs OAC for S3 origins

### OAC (Origin Access Control) — preferred

Modern approach. Supports SSE-KMS, all S3 features.

```hcl
resource "aws_cloudfront_origin_access_control" "oac" {
  name                              = "s3-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}
```

S3 bucket policy:
```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Service": "cloudfront.amazonaws.com"
    },
    "Action": "s3:GetObject",
    "Resource": "arn:aws:s3:::my-bucket/*",
    "Condition": {
      "StringEquals": {
        "AWS:SourceArn": "arn:aws:cloudfront::<ACCOUNT_ID>:distribution/E1234ABCDEF"
      }
    }
  }]
}
```

### OAI (Origin Access Identity) — legacy

Older approach. Does NOT support SSE-KMS encryption. Avoid for new distributions.

```hcl
# Legacy — do NOT use for new projects
resource "aws_cloudfront_origin_access_identity" "oai" {
  comment = "legacy-oai"
}
```

**Decision**: always use OAC for new S3 origins.

## ACM certificates — MUST be in us-east-1

CloudFront only accepts ACM certificates from `us-east-1`, regardless of where other resources are.

```hcl
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

resource "aws_acm_certificate" "cdn" {
  provider          = aws.us_east_1
  domain_name       = "portal.<org-domain>"
  validation_method = "DNS"

  subject_alternative_names = [
    "*.portal.<org-domain>"
  ]

  tags = {
    Environment = "PRD"
    CostCenter  = "<cost-center>"
    CostScope   = "FRONT-END"
    CostProject = "PORTAL"
    Name        = "portal-<org>-app-br-cert"
  }

  lifecycle {
    create_before_destroy = true
  }
}
```

## Cache policies

### AWS managed policies (use these first)

| Policy | Use for |
|--------|---------|
| `CachingOptimized` | Static assets (S3) — max caching |
| `CachingDisabled` | Dynamic APIs — pass-through |
| `CachingOptimizedForUncompressedObjects` | Large files without compression |

### Custom cache policy

```hcl
resource "aws_cloudfront_cache_policy" "custom" {
  name        = "<org>-api-cache"
  default_ttl = 60
  max_ttl     = 300
  min_ttl     = 0

  parameters_in_cache_key_and_forwarded_to_origin {
    cookies_config {
      cookie_behavior = "none"
    }
    headers_config {
      header_behavior = "whitelist"
      headers {
        items = ["Authorization"]
      }
    }
    query_strings_config {
      query_string_behavior = "whitelist"
      query_strings {
        items = ["version", "lang"]
      }
    }
    enable_accept_encoding_gzip  = true
    enable_accept_encoding_brotli = true
  }
}
```

## WAF integration

Attach AWS WAF WebACL to CloudFront for DDoS protection, rate limiting, and bot control.

```hcl
resource "aws_wafv2_web_acl" "cdn" {
  provider = aws.us_east_1  # WAF for CloudFront must be in us-east-1
  name     = "cdn-waf"
  scope    = "CLOUDFRONT"

  default_action {
    allow {}
  }

  rule {
    name     = "rate-limit"
    priority = 1

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = 2000
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      sampled_requests_enabled   = true
      cloudwatch_metrics_enabled = true
      metric_name                = "rate-limit"
    }
  }

  visibility_config {
    sampled_requests_enabled   = true
    cloudwatch_metrics_enabled = true
    metric_name                = "cdn-waf"
  }
}

resource "aws_cloudfront_distribution" "with_waf" {
  web_acl_id = aws_wafv2_web_acl.cdn.arn
  # ... rest of distribution config
}
```

## Signed URLs / Cookies

For private content (paid downloads, authenticated media):

```hcl
resource "aws_cloudfront_public_key" "signer" {
  name        = "content-signer"
  encoded_key = file("public_key.pem")
}

resource "aws_cloudfront_key_group" "signer" {
  name  = "content-signers"
  items = [aws_cloudfront_public_key.signer.id]
}
```

In cache behavior:
```hcl
default_cache_behavior {
  trusted_key_groups = [aws_cloudfront_key_group.signer.id]
  # ...
}
```

Generate signed URL in application code (private key stored in AWS Secrets Manager).

## <org>-specific patterns

### Terraform template

<org> uses a cookiecutter-style template for CloudFront distributions:

```
04-TERRAFORM/TEMPLATES/template-cloudfront/
├── main.tf
├── variables.tf
├── outputs.tf
├── terraform.tfvars.example
└── README.md
```

Shared CI template: `00-GITLAB/PIPELINES/gitlab-ci/cloud_front_template`.

### Standard distribution pattern at <org>

1. S3 bucket (private, SSE-KMS) for static assets
2. OAC (not OAI) for S3 access
3. ACM cert in `us-east-1` with DNS validation via Route 53
4. WAF WebACL with rate limiting
5. Custom error pages (403 → `/index.html` for SPAs)
6. Logging to S3 access log bucket

### Tagging

```hcl
tags = {
  Environment = "PRD"
  CostCenter  = "<cost-center>"       # or relevant team
  CostScope   = "FRONT-END"
  CostProject = "PORTAL-CLIENT"
  Name        = "APPS-PORTAL-CDN-PRD"
}
```

### SPA routing (React/Angular/Vue)

```hcl
custom_error_response {
  error_code         = 403
  response_code      = 200
  response_page_path = "/index.html"
}

custom_error_response {
  error_code         = 404
  response_code      = 200
  response_page_path = "/index.html"
}
```

## Cache invalidation

### When to invalidate

- Deploy new SPA version → invalidate `/*`
- Update specific asset → invalidate `/assets/main.js`
- First 1000 paths/month are free; after that $0.005/path

```bash
aws cloudfront create-invalidation \
  --distribution-id E1234ABCDEF \
  --paths "/*"
```

### Better alternative: versioned assets

Instead of invalidating, use content-hashed filenames:
```
/assets/main.a1b2c3d.js   # new deploy = new hash = new cache key
/index.html                # only this needs short TTL or invalidation
```

This avoids invalidation costs and propagation delay (invalidation takes 5-15 minutes globally).

## Useful commands

```bash
# List distributions
aws cloudfront list-distributions --query "DistributionList.Items[*].[Id,DomainName,Status]" --output table

# Get distribution config
aws cloudfront get-distribution-config --id E1234ABCDEF

# Check invalidation status
aws cloudfront get-invalidation --distribution-id E1234ABCDEF --id I1234

# Test cache behavior (check headers)
curl -I https://portal.<org-domain>/assets/main.js
# Look for: X-Cache: Hit from cloudfront
```

## Anti-patterns

- ❌ **Cache invalidation overuse** — invalidating `/*` on every deploy; use versioned filenames instead
- ❌ **OAI for new distributions** — use OAC (supports KMS, modern auth)
- ❌ **ACM cert not in us-east-1** — CloudFront rejects certs from other regions
- ❌ **WAF in wrong region** — WAF for CloudFront scope must be in `us-east-1`
- ❌ **Forwarding all headers** — destroys cache hit ratio (each unique header combo = cache miss)
- ❌ **No compression** — always enable gzip + brotli in cache policy
- ❌ **HTTP allowed** — use `redirect-to-https` viewer protocol policy
- ❌ **Missing custom error pages for SPAs** — React/Angular apps return 403 on direct URL access
- ❌ **Long TTL on index.html** — users get stale app; use short TTL (60s) for HTML, long for hashed assets
- ❌ **No WAF on public distributions** — exposed to DDoS and bot abuse
- ❌ **Manual distribution creation** — use Terraform template; manual drifts and lacks audit trail

## Reference

- CloudFront docs: https://docs.aws.amazon.com/AmazonCloudFront/
- OAC migration: https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-s3.html
- WAF docs: https://docs.aws.amazon.com/waf/
- Related: `route53-patterns`, `terraform-modules-bdc`, `iam-patterns`
