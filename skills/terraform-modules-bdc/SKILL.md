---
name: terraform-modules-bdc
description: "BDC Terraform modules catalog. Use when provisioning AWS resources (ECS services/clusters, S3 buckets, CloudFront distributions, EC2 instances) or migrating ECS to EKS. Covers module interfaces, common variables, tagging patterns, and usage examples."
keywords: [terraform-modules, terraform, modules, "terraform modules", cloudfront, ec2, eks, s3, aws, ecs]
---
# BDC Terraform Modules

## Overview

BDC maintains reusable Terraform modules, cookiecutter templates, and live resource definitions for all AWS infrastructure. All provisioning follows Infrastructure-as-Code principles with mandatory tagging.

## Directory Structure

```
04-TERRAFORM/
├── MODULES/                    # Reusable modules (source of truth)
│   ├── aws-ecs-service/
│   ├── aws-ecs-cluster/
│   ├── aws-ecs-scheduledtask/
│   ├── aws-s3-bucket/
│   ├── aws-cloudfront/
│   └── ecs-to-eks-migration/
├── TEMPLATES/                  # Cookiecutter-style scaffolding
│   ├── template-ecs-service/
│   ├── template-ecs-cluster/
│   ├── template-ecs-scheduledtask/
│   └── template-ec2-instance/
└── RESOURCES/                  # Live infrastructure definitions
    ├── eks-clusters/
    ├── networking/
    ├── s3-buckets/
    └── iam-roles/
```

## Available Modules

### aws-ecs-service

The most complex module. Provisions a complete ECS service with all supporting resources.

**Features**:
- Task definition (Fargate or EC2 launch type)
- ALB target group + listener rules
- Service discovery (Cloud Map)
- Autoscaling (target tracking, step scaling, scheduled)
- IAM task role + execution role
- CloudWatch log group
- Security groups

**Key variables**:

```hcl
variable "service_name" { type = string }
variable "cluster_name" { type = string }
variable "container_image" { type = string }
variable "container_port" { type = number }
variable "desired_count" { type = number, default = 2 }
variable "cpu" { type = number, default = 256 }
variable "memory" { type = number, default = 512 }

# Autoscaling
variable "autoscaling_enabled" { type = bool, default = true }
variable "autoscaling_min" { type = number, default = 2 }
variable "autoscaling_max" { type = number, default = 10 }
variable "autoscaling_type" { type = string, default = "target_tracking" }
# Options: "target_tracking" | "step_scaling" | "scheduled"

# ALB
variable "alb_arn" { type = string }
variable "health_check_path" { type = string, default = "/healthz" }

# Mandatory tags
variable "tags" { type = map(string) }
```

**Usage**:

```hcl
module "people_api" {
  source = "../../MODULES/aws-ecs-service"

  service_name    = "dpm-people-api"
  cluster_name    = "bdc-ecs-prd"
  container_image = "harbor.bigdatacorp.com.br/bdc-images/dpm-people-api:a1b2c3d"
  container_port  = 8080
  desired_count   = 3
  cpu             = 512
  memory          = 1024

  autoscaling_enabled = true
  autoscaling_min     = 2
  autoscaling_max     = 20
  autoscaling_type    = "target_tracking"

  alb_arn           = data.aws_lb.main.arn
  health_check_path = "/healthz"

  tags = {
    Environment = "PRD"
    CostCenter  = "Program-DataPlatform"
    CostScope   = "API"
    CostProject = "PEOPLE"
    Name        = "DPM-PEOPLE-API-PRD"
  }
}
```

### aws-ecs-cluster

Provisions an ECS cluster with capacity providers.

```hcl
module "prd_cluster" {
  source = "../../MODULES/aws-ecs-cluster"

  cluster_name       = "bdc-ecs-prd"
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy = [
    { capacity_provider = "FARGATE", weight = 1, base = 2 },
    { capacity_provider = "FARGATE_SPOT", weight = 3 }
  ]

  tags = {
    Environment = "PRD"
    CostCenter  = "Platform-Infrastructure"
    CostScope   = "INFRASTRUCTURE"
    CostProject = "ECS"
    Name        = "bdc-ecs-prd"
  }
}
```

### aws-ecs-scheduledtask

ECS scheduled tasks (EventBridge rule + ECS RunTask).

```hcl
module "nightly_sync" {
  source = "../../MODULES/aws-ecs-scheduledtask"

  task_name         = "dpm-nightly-sync"
  cluster_arn       = module.prd_cluster.arn
  schedule          = "cron(0 3 * * ? *)"  # 03:00 UTC daily
  container_image   = "harbor.bigdatacorp.com.br/bdc-images/dpm-sync:a1b2c3d"
  cpu               = 1024
  memory            = 2048

  tags = {
    Environment = "BTC"
    CostCenter  = "Program-DataPlatform"
    CostScope   = "PROCESS"
    CostProject = "PEOPLE-BATCH"
    Name        = "DPM-NIGHTLY-SYNC-BTC"
  }
}
```

### aws-s3-bucket

S3 bucket with BDC security defaults.

**Features**:
- Lifecycle rules (transition to IA/Glacier, expiration)
- Versioning (enabled by default)
- Encryption (SSE-S3 or SSE-KMS)
- Cross-region replication (optional)
- Bucket policy (deny non-TLS)
- Public access block (always enabled)

```hcl
module "data_lake" {
  source = "../../MODULES/aws-s3-bucket"

  bucket_name = "bdc-dpm-data-lake-prd"
  versioning  = true
  encryption  = "aws:kms"
  kms_key_arn = data.aws_kms_key.main.arn

  lifecycle_rules = [
    {
      id      = "archive-old"
      prefix  = "raw/"
      transition_days    = 90
      transition_class   = "GLACIER"
      expiration_days    = 365
    }
  ]

  replication = {
    enabled     = true
    destination = "arn:aws:s3:::bdc-dpm-data-lake-prd-replica"
    role_arn    = aws_iam_role.replication.arn
  }

  tags = {
    Environment = "PRD"
    CostCenter  = "Program-DataPlatform"
    CostScope   = "STORAGE"
    CostProject = "DATA-LAKE"
    Name        = "bdc-dpm-data-lake-prd"
  }
}
```

### aws-cloudfront

CloudFront distribution for frontend applications.

**Features**:
- Multiple behaviors (static + API proxy)
- Custom cache policies
- Origin access control (OAC) for S3
- ACM certificate integration
- WAF association (optional)

```hcl
module "portal_cdn" {
  source = "../../MODULES/aws-cloudfront"

  domain_aliases = ["portal.bdc.app.br"]
  acm_cert_arn   = data.aws_acm_certificate.portal.arn

  origins = [
    {
      id          = "s3-static"
      domain_name = module.portal_bucket.regional_domain_name
      type        = "s3"
      oac_enabled = true
    },
    {
      id          = "api-backend"
      domain_name = "api.bdc.internal"
      type        = "custom"
      protocol    = "https-only"
    }
  ]

  behaviors = [
    {
      path_pattern = "/api/*"
      origin_id    = "api-backend"
      cache_policy = "CachingDisabled"
    }
  ]

  tags = {
    Environment = "PRD"
    CostCenter  = "Program-Apps"
    CostScope   = "FRONT-END"
    CostProject = "PORTAL"
    Name        = "APPS-PORTAL-CDN-PRD"
  }
}
```

### ecs-to-eks-migration

Python tool that converts ECS terraform variable files to EKS Helm values.

**Purpose**: Automate the ECS-to-EKS migration by translating:
- ECS task definition CPU/memory to K8s resource requests/limits
- ECS service desired count to KEDA minReplicas
- ECS environment variables to ConfigMap/ExternalSecret
- ECS secrets to ExternalSecret remoteRef
- ALB health check to K8s probes
- ECS autoscaling to KEDA triggers

**Usage**:

```bash
cd 04-TERRAFORM/MODULES/ecs-to-eks-migration/
python migrate.py \
  --input ../RESOURCES/ecs-services/dpm-people-api.tfvars \
  --output /tmp/dpm-people-api-values.yaml \
  --chart app
```

## Templates (Cookiecutter)

Scaffolding for new infrastructure. Generate a new service definition:

| Template | Creates |
|----------|---------|
| `template-ecs-service` | Complete ECS service tfvars + backend config |
| `template-ecs-cluster` | ECS cluster with capacity providers |
| `template-ecs-scheduledtask` | Scheduled task with EventBridge |
| `template-ec2-instance` | EC2 instance with security group + IAM |

## Common Variables Pattern

ALL modules require mandatory tags:

```hcl
variable "tags" {
  type = map(string)
  description = "Mandatory tags for all resources"

  validation {
    condition = alltrue([
      contains(keys(var.tags), "Environment"),
      contains(keys(var.tags), "CostCenter"),
      contains(keys(var.tags), "CostScope"),
      contains(keys(var.tags), "CostProject"),
      contains(keys(var.tags), "Name"),
    ])
    error_message = "Tags must include: Environment, CostCenter, CostScope, CostProject, Name"
  }
}
```

## State Management

All Terraform state uses S3 backend with DynamoDB locking:

```hcl
terraform {
  backend "s3" {
    bucket         = "bdc-terraform-state"
    key            = "ecs-services/dpm-people-api/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "bdc-terraform-locks"
    encrypt        = true
  }
}
```

State key convention: `<resource-type>/<service-name>/terraform.tfstate`

## Naming Convention

Resources follow: `<SIGLA>-<PROJECT>-<SCOPE>-<ENV>`

| Component | Source | Example |
|-----------|--------|---------|
| SIGLA | Team short code | `DPM`, `DCP`, `MDT` |
| PROJECT | CostProject value | `PEOPLE`, `RECEITA-FEDERAL` |
| SCOPE | CostScope abbreviation | `API`, `PRC` (process), `CDN` |
| ENV | Environment | `PRD`, `DEV`, `HML`, `BTC` |

Examples:
- `DPM-PEOPLE-API-PRD` — Data Platform People API in production
- `DCP-RECEITA-PRC-DEV` — Data Capture Receita processor in dev
- `APPS-PORTAL-CDN-PRD` — Apps Portal CDN in production

## Resources Directory

Live infrastructure definitions (not modules):

| Path | Content |
|------|---------|
| `RESOURCES/eks-clusters/` | EKS cluster definitions (Karpenter, node groups) |
| `RESOURCES/networking/` | VPCs, subnets, NAT gateways, transit gateway |
| `RESOURCES/s3-buckets/` | All S3 bucket instantiations |
| `RESOURCES/iam-roles/` | IAM roles (IRSA, CI/CD, cross-account) |

## Anti-patterns

- Hardcoded AWS account IDs (use `data.aws_caller_identity` or variables)
- Missing mandatory tags (breaks FinOps reporting and compliance)
- Wildcard IAM policies (`"Resource": "*"`) without documented justification
- No state locking (risk of concurrent apply corruption)
- Inline policies instead of managed policies (harder to audit)
- Resources without `Name` tag (invisible in AWS console)
- Using `terraform apply` without prior `plan` review
- Storing state locally (must use S3 backend)
- Hardcoded regions (use variables for multi-region readiness)
- Skipping `validation` blocks on critical variables
- Creating resources outside modules (one-off `.tf` files without reuse)
