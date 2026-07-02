terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

locals {
  base_tags = merge(var.tags, {
    Module    = "iam"
    ManagedBy = "terraform"
    Component = var.name_prefix
  })
}

# =====================================================================
# IAM Role + IRSA trust policy (EKS ServiceAccount -> AWS role)
#
# Single role assumed by the supervisor pod's ServiceAccount. All
# capability policies below attach to this role. The app is read-only
# by design (docs/READ_ONLY_POLICY.md); the ONLY write permission is
# DynamoDB PutItem on the sessions table (conversation history).
# =====================================================================

data "aws_iam_policy_document" "irsa_trust" {
  statement {
    sid     = "EKSPodAssumeRole"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.eks_oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.eks_oidc_provider_url}:sub"
      values   = ["system:serviceaccount:${var.k8s_namespace}:${var.k8s_service_account}"]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.eks_oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "this" {
  name               = "${var.name_prefix}-irsa"
  description        = "IRSA role for ${var.name_prefix} supervisor pod"
  assume_role_policy = data.aws_iam_policy_document.irsa_trust.json
  tags               = local.base_tags
}

# ---------------------------------------------------------------------
# Bedrock — invoke foundation models (Claude/Titan) + list
# ---------------------------------------------------------------------

data "aws_iam_policy_document" "bedrock" {
  statement {
    sid    = "InvokeFoundationModels"
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = var.allowed_model_arns
  }

  statement {
    sid    = "ListFoundationModels"
    effect = "Allow"
    actions = [
      "bedrock:ListFoundationModels",
      "bedrock:GetFoundationModel",
    ]
    resources = ["*"]
  }

  # Apply the anti-prompt-injection guardrail (spec 14 L1). Scoped to the
  # guardrail ARN when provided; falls back to account guardrails otherwise.
  dynamic "statement" {
    for_each = var.guardrail_arn != "" ? [1] : []
    content {
      sid       = "ApplyGuardrail"
      effect    = "Allow"
      actions   = ["bedrock:ApplyGuardrail"]
      resources = [var.guardrail_arn]
    }
  }
}

resource "aws_iam_policy" "bedrock" {
  name        = "${var.name_prefix}-bedrock"
  description = "Invoke Bedrock foundation models"
  policy      = data.aws_iam_policy_document.bedrock.json
  tags        = local.base_tags
}

resource "aws_iam_role_policy_attachment" "bedrock" {
  role       = aws_iam_role.this.name
  policy_arn = aws_iam_policy.bedrock.arn
}

# ---------------------------------------------------------------------
# DynamoDB — sessions table (the ONLY write permission in the app)
# ---------------------------------------------------------------------

data "aws_iam_policy_document" "dynamodb" {
  statement {
    sid    = "SessionHistory"
    effect = "Allow"
    actions = [
      "dynamodb:PutItem",
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:DescribeTable",
    ]
    resources = [
      var.sessions_table_arn,
      "${var.sessions_table_arn}/index/*",
    ]
  }
}

resource "aws_iam_policy" "dynamodb" {
  name        = "${var.name_prefix}-dynamodb-sessions"
  description = "Read/write conversation history on the sessions table"
  policy      = data.aws_iam_policy_document.dynamodb.json
  tags        = local.base_tags
}

resource "aws_iam_role_policy_attachment" "dynamodb" {
  role       = aws_iam_role.this.name
  policy_arn = aws_iam_policy.dynamodb.arn
}

# ---------------------------------------------------------------------
# Read-only inventory (Boto3Adapter in src/core/adapters.py)
# ec2/s3/rds describe+list, Cost Explorer, iam list/get. No wildcards.
# ---------------------------------------------------------------------

data "aws_iam_policy_document" "inventory" {
  statement {
    sid    = "DescribeReadOnly"
    effect = "Allow"
    actions = [
      "ec2:DescribeInstances",
      "ec2:DescribeInstanceStatus",
      "rds:DescribeDBInstances",
      "rds:DescribeDBClusters",
      "s3:ListAllMyBuckets",
      "s3:GetBucketLocation",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "CostExplorerReadOnly"
    effect = "Allow"
    actions = [
      "ce:GetCostAndUsage",
      "ce:GetCostForecast",
      "ce:GetDimensionValues",
      "ce:GetTags",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "IamReadOnly"
    effect = "Allow"
    actions = [
      "iam:ListRoles",
      "iam:GetRole",
      "iam:ListPolicies",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "inventory" {
  name        = "${var.name_prefix}-inventory-readonly"
  description = "Read-only inventory collection for agent context"
  policy      = data.aws_iam_policy_document.inventory.json
  tags        = local.base_tags
}

resource "aws_iam_role_policy_attachment" "inventory" {
  role       = aws_iam_role.this.name
  policy_arn = aws_iam_policy.inventory.arn
}

# ---------------------------------------------------------------------
# FinOps via Athena/CUR (OPTIONAL, disabled by default).
#
# The CUR may live in a separate payer account (cross-account read is a
# Phase 2 concern: the bucket policy must be granted on the payer side,
# or the CUR replicated into this account). For now FinOps uses Cost
# Explorer above on the local account. Enable this only when an Athena
# CUR target exists and the bucket ARNs are known.
# ---------------------------------------------------------------------

data "aws_iam_policy_document" "athena_finops" {
  count = var.enable_athena_finops ? 1 : 0

  statement {
    sid    = "AthenaQuery"
    effect = "Allow"
    actions = [
      "athena:StartQueryExecution",
      "athena:StopQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:GetWorkGroup",
    ]
    resources = [
      "arn:aws:athena:${var.region}:${var.account_id}:workgroup/${var.athena_workgroup}",
    ]
  }

  statement {
    sid    = "GlueCatalogReadOnly"
    effect = "Allow"
    actions = [
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:GetTable",
      "glue:GetTables",
      "glue:GetPartition",
      "glue:GetPartitions",
    ]
    resources = [
      "arn:aws:glue:${var.region}:${var.account_id}:catalog",
      "arn:aws:glue:${var.region}:${var.account_id}:database/${var.athena_database}",
      "arn:aws:glue:${var.region}:${var.account_id}:table/${var.athena_database}/*",
    ]
  }

  # CUR source bucket (read) — ARNs supplied via var.cur_s3_bucket_arns.
  statement {
    sid    = "CurBucketRead"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:ListBucket",
    ]
    resources = var.cur_s3_bucket_arns
  }

  # Athena query results bucket (read + write).
  statement {
    sid    = "AthenaResultsBucket"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = [
      var.athena_results_bucket_arn,
      "${var.athena_results_bucket_arn}/*",
    ]
  }
}

resource "aws_iam_policy" "athena_finops" {
  count       = var.enable_athena_finops ? 1 : 0
  name        = "${var.name_prefix}-athena-finops"
  description = "Athena + Glue + S3 read access for CUR-based FinOps queries"
  policy      = data.aws_iam_policy_document.athena_finops[0].json
  tags        = local.base_tags
}

resource "aws_iam_role_policy_attachment" "athena_finops" {
  count      = var.enable_athena_finops ? 1 : 0
  role       = aws_iam_role.this.name
  policy_arn = aws_iam_policy.athena_finops[0].arn
}
