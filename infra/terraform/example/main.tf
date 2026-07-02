terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

provider "aws" {
  region = var.region

  # Tags applied to every taggable resource in all modules. Only ManagedBy is
  # set by default; add org-specific tags (cost allocation, environment, etc.)
  # via var.tags — the chart/infra impose no org-specific tagging scheme.
  default_tags {
    tags = merge({ ManagedBy = "terraform" }, var.tags)
  }
}

variable "tags" {
  description = "Extra tags merged into provider default_tags (e.g. cost-allocation tags). Optional."
  type        = map(string)
  default     = {}
}

variable "region" {
  default = "us-east-1"
}

# In a real setup, these would come from a remote state of the EKS module.
# For this example, fill them with values from your cluster.
variable "eks_cluster_name" {
  type    = string
  default = "my-cluster"
}

variable "vpc_id" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "eks_worker_security_group_id" {
  type = string
}

# Must match the ACTUAL namespace + ServiceAccount the supervisor pod runs as.
# The Helm chart names the SA <release>-supervisor (serviceFullname), not the
# bare release name — so the IRSA trust `sub` must target that exact SA.
# Defaults are neutral; override per environment (e.g. namespace "staffops").
variable "k8s_namespace" {
  type    = string
  default = "aigent-squad"
}

variable "k8s_service_account" {
  type    = string
  default = "aigent-squad-supervisor"
}

variable "bedrock_model_profiles" {
  description = <<-EOT
    Map of model_key => system inference profile id to create an AIP for.
    Add/remove entries to control how many/which models get a cost-tagged
    Application Inference Profile. The id is the SYSTEM profile (the "us."
    one); the full ARN is built from region + account automatically.
    Discover ids: aws bedrock list-inference-profiles --query \
      "inferenceProfileSummaries[?type=='SYSTEM_DEFINED'].inferenceProfileId"
  EOT
  type        = map(string)
  default = {
    sonnet45 = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    # haiku45  = "us.anthropic.claude-haiku-4-5-20251001-v1:0"   # e.g. for the classifier tier (spec 11)
    # opus45   = "us.anthropic.claude-opus-4-5-20251101-v1:0"
  }
}

# -----------------------------------------------------------------------
# Lookups: account ID + EKS OIDC provider (created with the cluster)
# -----------------------------------------------------------------------

data "aws_caller_identity" "current" {}

data "aws_eks_cluster" "this" {
  name = var.eks_cluster_name
}

locals {
  oidc_url = replace(data.aws_eks_cluster.this.identity[0].oidc[0].issuer, "https://", "")
}

data "aws_iam_openid_connect_provider" "eks" {
  url = data.aws_eks_cluster.this.identity[0].oidc[0].issuer
}

# -----------------------------------------------------------------------
# Security group for the Bedrock VPC endpoints
# -----------------------------------------------------------------------

resource "aws_security_group" "bedrock_endpoint" {
  name        = "aigent-squad-bedrock-endpoint"
  description = "Allow EKS workers to reach Bedrock VPC endpoints"
  vpc_id      = var.vpc_id

  ingress {
    description     = "HTTPS from EKS workers"
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [var.eks_worker_security_group_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "aigent-squad-bedrock-endpoint"
  }
}

# -----------------------------------------------------------------------
# DynamoDB — conversation/session history
# -----------------------------------------------------------------------

module "dynamodb" {
  source = "../dynamodb"

  name_prefix = "aigent-squad"
  table_name  = "agent-sessions" # must match env DYNAMODB_SESSIONS_TABLE

  enable_point_in_time_recovery = true
  enable_deletion_protection    = false # set true in PRD
}

# -----------------------------------------------------------------------
# Bedrock Guardrail — anti-prompt-injection (spec 14 L1)
# -----------------------------------------------------------------------

module "guardrail" {
  source = "../guardrail"

  name_prefix = "aigent-squad"
  # PROMPT_ATTACK HIGH can over-block legitimate ops queries; MEDIUM is a
  # safer default for a consultative read-only assistant. Tune per feedback.
  prompt_attack_strength = "MEDIUM"
}

# -----------------------------------------------------------------------
# IAM — single IRSA role with all capability policies
# -----------------------------------------------------------------------

module "iam" {
  source = "../iam"

  name_prefix = "aigent-squad"
  region      = var.region
  account_id  = data.aws_caller_identity.current.account_id

  # IRSA wiring
  eks_oidc_provider_arn = data.aws_iam_openid_connect_provider.eks.arn
  eks_oidc_provider_url = local.oidc_url
  k8s_namespace         = var.k8s_namespace
  k8s_service_account   = var.k8s_service_account

  # DynamoDB sessions table (the only write permission)
  sessions_table_arn = module.dynamodb.table_arn

  # Guardrail — allow ApplyGuardrail on the provisioned guardrail (spec 14).
  guardrail_arn = module.guardrail.guardrail_arn

  # FinOps: Cost Explorer is always on (own account). Athena/CUR is off
  # until a CUR target exists (CUR may live in the payer account).
  enable_athena_finops = false
  # athena_workgroup          = "primary"
  # athena_database           = "cur_db"
  # cur_s3_bucket_arns        = ["arn:aws:s3:::my-cur-bucket", "arn:aws:s3:::my-cur-bucket/*"]
  # athena_results_bucket_arn = "arn:aws:s3:::my-athena-results"
}

# -----------------------------------------------------------------------
# Bedrock — VPC endpoints (+ optional invocation logging)
# -----------------------------------------------------------------------

module "bedrock" {
  source = "../bedrock"

  name_prefix = "aigent-squad"
  region      = var.region

  vpc_id             = var.vpc_id
  subnet_ids         = var.private_subnet_ids
  security_group_ids = [aws_security_group.bedrock_endpoint.id]

  # enable_invocation_logging = true
}

# -----------------------------------------------------------------------
# Bedrock — Application Inference Profiles for cost attribution (spec 27)
# One AIP per model, carrying FinOps cost allocation tags. The app uses
# the AIP ARN as BEDROCK_MODEL_ID so spend is taggable in Cost Explorer.
# -----------------------------------------------------------------------

module "bedrock_aip" {
  source = "../bedrock-aip"

  name_prefix = "aigent-squad"

  # Build full system-profile ARNs (region + account) from the configurable
  # map of model_key => system inference profile id. Add/remove entries in
  # var.bedrock_model_profiles to control how many/which AIPs are created.
  models = {
    for key, profile_id in var.bedrock_model_profiles :
    key => "arn:aws:bedrock:${var.region}:${data.aws_caller_identity.current.account_id}:inference-profile/${profile_id}"
  }
}

# -----------------------------------------------------------------------
# Outputs
# -----------------------------------------------------------------------

output "irsa_role_arn" {
  value       = module.iam.role_arn
  description = "Pass to Helm: --set serviceAccount.annotations.\"eks\\.amazonaws\\.com/role-arn\"=<this>"
}

output "service_account_annotation" {
  value = module.iam.service_account_annotation
}

output "sessions_table_name" {
  value       = module.dynamodb.table_name
  description = "Set env DYNAMODB_SESSIONS_TABLE to this value"
}

output "guardrail_id" {
  value       = module.guardrail.guardrail_id
  description = "Set env GUARDRAIL_ID to this value (spec 14)"
}

output "guardrail_version" {
  value       = module.guardrail.guardrail_version
  description = "Set env GUARDRAIL_VERSION to this value (spec 14)"
}

output "bedrock_runtime_endpoint_id" {
  value = module.bedrock.vpc_endpoint_runtime_id
}

output "bedrock_aip_arns" {
  description = "Model => Application Inference Profile ARN. Set BEDROCK_MODEL_ID to the relevant ARN."
  value       = module.bedrock_aip.inference_profile_arns
}
