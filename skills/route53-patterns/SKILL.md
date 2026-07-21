---
name: route53-patterns
description: Use when configuring DNS zones, routing policies, health checks, or External-DNS integration with EKS. Covers public/private hosted zones, routing policies, subdomain delegation, ACME DNS-01 validation, and <org> context.
keywords: [route53, dns, eks, external-dns, acme]
---
# AWS Route 53 Patterns

DNS management patterns for <org>'s AWS environments.

## <org> DNS layout

| Zone | Type | Purpose |
|------|------|---------|
| `<org-domain>` | Public | External-facing services, APIs, frontends |
| `<old-internal-domain>` | Private (VPC-associated) | Internal service discovery, cross-cluster communication |

Private zone `<old-internal-domain>` is associated with all VPCs in `us-east-1`. Certificates for `*.<old-internal-domain>` are issued by AWS Private CA via cert-manager.

## Core concepts

### Hosted zones

A hosted zone is a container for DNS records of a domain. Route 53 charges per hosted zone ($0.50/month) + per query.

```hcl
resource "aws_route53_zone" "public" {
  name = "<org-domain>"

  tags = {
    Environment = "PRD"
    CostCenter  = "<cost-center>"
    CostScope   = "INFRASTRUCTURE"
    CostProject = "DNS"
    Name        = "<org>-app-br-public-zone"
  }
}

resource "aws_route53_zone" "private" {
  name = "<old-internal-domain>"

  vpc {
    vpc_id = var.vpc_id
  }

  tags = {
    Environment = "PRD"
    CostCenter  = "<cost-center>"
    CostScope   = "INFRASTRUCTURE"
    CostProject = "DNS"
    Name        = "<org>-internal-private-zone"
  }
}
```

### Record types

| Type | Use |
|------|-----|
| `A` | IPv4 address (or alias to AWS resource) |
| `AAAA` | IPv6 address |
| `CNAME` | Canonical name (cannot be at zone apex) |
| `ALIAS` | AWS-specific — points to ALB, CloudFront, S3, etc. (free queries, works at apex) |
| `TXT` | Verification, SPF, DKIM, ACME challenges |
| `MX` | Mail routing |
| `SRV` | Service discovery (rare — prefer K8s DNS) |
| `NS` | Subdomain delegation |

**Prefer ALIAS over CNAME** for AWS resources — no query charges, works at zone apex.

## Routing policies

### Simple routing
One record, one or more values. Route 53 returns all values (client picks randomly).

```hcl
resource "aws_route53_record" "api" {
  zone_id = aws_route53_zone.public.zone_id
  name    = "api.<org-domain>"
  type    = "A"

  alias {
    name                   = aws_lb.api.dns_name
    zone_id                = aws_lb.api.zone_id
    evaluate_target_health = true
  }
}
```

### Weighted routing
Distribute traffic by percentage. Useful for canary deployments or A/B testing.

```hcl
resource "aws_route53_record" "api_v1" {
  zone_id        = aws_route53_zone.public.zone_id
  name           = "api.<org-domain>"
  type           = "A"
  set_identifier = "v1"

  weighted_routing_policy {
    weight = 90
  }

  alias {
    name                   = aws_lb.api_v1.dns_name
    zone_id                = aws_lb.api_v1.zone_id
    evaluate_target_health = true
  }
}

resource "aws_route53_record" "api_v2" {
  zone_id        = aws_route53_zone.public.zone_id
  name           = "api.<org-domain>"
  type           = "A"
  set_identifier = "v2"

  weighted_routing_policy {
    weight = 10
  }

  alias {
    name                   = aws_lb.api_v2.dns_name
    zone_id                = aws_lb.api_v2.zone_id
    evaluate_target_health = true
  }
}
```

### Failover routing
Active-passive. Primary receives traffic unless health check fails.

```hcl
resource "aws_route53_record" "primary" {
  zone_id        = aws_route53_zone.public.zone_id
  name           = "app.<org-domain>"
  type           = "A"
  set_identifier = "primary"

  failover_routing_policy {
    type = "PRIMARY"
  }

  alias {
    name                   = aws_lb.primary.dns_name
    zone_id                = aws_lb.primary.zone_id
    evaluate_target_health = true
  }

  health_check_id = aws_route53_health_check.primary.id
}
```

### Latency-based routing
Route to the region with lowest latency for the client. Useful for multi-region (future <org> roadmap).

### Geolocation routing
Route based on client geographic location. Useful for compliance (data residency) or localized content.

## Health checks

Health checks monitor endpoint availability and trigger failover.

```hcl
resource "aws_route53_health_check" "api" {
  fqdn              = "api.<org-domain>"
  port              = 443
  type              = "HTTPS"
  resource_path     = "/healthz"
  failure_threshold = 3
  request_interval  = 30

  tags = {
    Environment = "PRD"
    CostCenter  = "<cost-center>"
    CostScope   = "MONITORING"
    CostProject = "DNS"
    Name        = "api-<org>-health-check"
  }
}

# CloudWatch alarm on health check
resource "aws_cloudwatch_metric_alarm" "dns_health" {
  alarm_name          = "route53-api-unhealthy"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "HealthCheckStatus"
  namespace           = "AWS/Route53"
  period              = 60
  statistic           = "Minimum"
  threshold           = 1

  dimensions = {
    HealthCheckId = aws_route53_health_check.api.id
  }

  alarm_actions = [var.sns_topic_arn]
}
```

## External-DNS on EKS

External-DNS automatically creates Route 53 records from K8s Ingress/Service annotations.

### Deployment (<org> uses helmfile)

```yaml
# helmfile values
provider: aws
domainFilters:
  - <org-domain>
  - <old-internal-domain>
policy: sync          # create + delete records
txtOwnerId: "<org>-eks-core"
sources:
  - service
  - ingress
  - gateway-httproute
  - gateway-grpcroute
```

### IRSA for External-DNS

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "route53:ChangeResourceRecordSets"
      ],
      "Resource": [
        "arn:aws:route53:::hostedzone/Z1234PUBLIC",
        "arn:aws:route53:::hostedzone/Z5678PRIVATE"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "route53:ListHostedZones",
        "route53:ListResourceRecordSets",
        "route53:ListTagsForResource"
      ],
      "Resource": "*"
    }
  ]
}
```

### Annotations for External-DNS

```yaml
apiVersion: v1
kind: Service
metadata:
  name: my-api
  annotations:
    external-dns.alpha.kubernetes.io/hostname: "my-api.<org-domain>"
    external-dns.alpha.kubernetes.io/ttl: "300"
spec:
  type: LoadBalancer
  # ...
```

For Gateway API (<org> preferred):
```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: my-api
  annotations:
    external-dns.alpha.kubernetes.io/hostname: "my-api.<org-domain>"
spec:
  parentRefs:
    - name: istio-gateway
      namespace: istio-gateway
  # ...
```

## Subdomain delegation

Delegate a subdomain to another hosted zone (e.g., team-managed zone):

```hcl
# In parent zone (<org-domain>)
resource "aws_route53_record" "delegate_team" {
  zone_id = aws_route53_zone.public.zone_id
  name    = "team.<org-domain>"
  type    = "NS"
  ttl     = 172800

  records = [
    aws_route53_zone.team.name_servers[0],
    aws_route53_zone.team.name_servers[1],
    aws_route53_zone.team.name_servers[2],
    aws_route53_zone.team.name_servers[3],
  ]
}
```

## ACME DNS-01 validation (cert-manager)

cert-manager uses DNS-01 challenges for wildcard certs and internal domains.

```yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    privateKeySecretRef:
      name: letsencrypt-prod-key
    solvers:
      - dns01:
          route53:
            region: us-east-1
            hostedZoneID: Z1234PUBLIC
```

cert-manager's ServiceAccount needs IRSA with `route53:ChangeResourceRecordSets` on the zone.

## <org>-specific patterns

### Clusters and DNS

| Cluster | DNS zone | Records managed by |
|---------|----------|-------------------|
| `core-devops` | `<old-internal-domain>` + `<org-domain>` | External-DNS |
| `prd-nv` | `<org-domain>` | External-DNS |
| `dev` | `<org-domain>` (dev subdomains) | External-DNS |

### txtOwnerId per cluster

Each cluster's External-DNS uses a unique `txtOwnerId` to avoid record conflicts:
- `core-devops` → `<org>-eks-core`
- `prd-nv` → `<org>-eks-prd-nv`
- `dev` → `<org>-eks-dev`

### Internal service endpoints

Cross-cluster services use `*.<old-internal-domain>` with private hosted zone:
- `otelcollector-prd.<old-internal-domain>`
- `otel-mdt.<old-internal-domain>`
- `victoria-metrics-read.<org-domain>`

### Tagging

All Route 53 resources follow <org> mandatory tags:
- `Environment`: `PRD` (DNS is shared infra)
- `CostCenter`: `<cost-center>`
- `CostScope`: `INFRASTRUCTURE`
- `CostProject`: `DNS`
- `Name`: descriptive

## Useful commands

```bash
# List hosted zones
aws route53 list-hosted-zones --output table

# List records in a zone
aws route53 list-resource-record-sets --hosted-zone-id Z1234PUBLIC

# Test DNS resolution
aws route53 test-dns-answer \
  --hosted-zone-id Z1234PUBLIC \
  --record-name api.<org-domain> \
  --record-type A

# Check health check status
aws route53 get-health-check-status --health-check-id HC123

# External-DNS logs
kubectl logs -n external-dns -l app.kubernetes.io/name=external-dns --tail=50
```

## Anti-patterns

- ❌ **TTL too low (<60s)** — increases query costs and Route 53 charges; use 300s minimum for stable records
- ❌ **Zone proliferation** — creating a hosted zone per service; consolidate into `<org-domain>` and `<old-internal-domain>`
- ❌ **CNAME at zone apex** — DNS spec forbids it; use ALIAS records for AWS resources
- ❌ **Missing health checks on failover records** — failover won't trigger without health checks
- ❌ **Wildcard records without intent** — `*.<org-domain>` catches all subdomains, may conflict with External-DNS
- ❌ **Manual record creation** — use External-DNS or Terraform; manual records drift and conflict
- ❌ **Same txtOwnerId across clusters** — External-DNS instances will fight over records
- ❌ **Private zone not associated with VPC** — pods can't resolve `*.<old-internal-domain>`
- ❌ **No `evaluate_target_health` on ALIAS** — unhealthy targets still receive traffic

## Reference

- Route 53 docs: https://docs.aws.amazon.com/Route53/
- External-DNS: https://github.com/kubernetes-sigs/external-dns
- cert-manager DNS01: https://cert-manager.io/docs/configuration/acme/dns01/route53/
- Related: `eks-management`, `iam-patterns`, `istio-ambient-otel`
