terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  base_tags = merge(var.tags, {
    Module    = "bedrock"
    ManagedBy = "terraform"
    Component = var.name_prefix
  })
}

# =====================================================================
# VPC Endpoints (private connectivity from EKS to Bedrock)
#
# IRSA role/policy for invoking Bedrock now lives in the iam/ module.
# This module only handles network plumbing + invocation logging.
# =====================================================================

resource "aws_vpc_endpoint" "bedrock_runtime" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${var.region}.bedrock-runtime"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.subnet_ids
  security_group_ids  = var.security_group_ids
  private_dns_enabled = true

  tags = merge(local.base_tags, {
    Name = "${var.name_prefix}-bedrock-runtime-endpoint"
  })
}

# Bedrock control plane endpoint (for ListFoundationModels etc).
# Optional but recommended for fully-private setups.
resource "aws_vpc_endpoint" "bedrock" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${var.region}.bedrock"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.subnet_ids
  security_group_ids  = var.security_group_ids
  private_dns_enabled = true

  tags = merge(local.base_tags, {
    Name = "${var.name_prefix}-bedrock-endpoint"
  })
}

# =====================================================================
# Invocation logging (OPTIONAL, disabled by default)
# =====================================================================

resource "aws_cloudwatch_log_group" "bedrock_invocations" {
  count             = var.enable_invocation_logging ? 1 : 0
  name              = "/aws/bedrock/${var.name_prefix}/invocations"
  retention_in_days = var.log_retention_days
  tags              = local.base_tags
}

data "aws_iam_policy_document" "bedrock_logging_trust" {
  count = var.enable_invocation_logging ? 1 : 0

  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "bedrock_logging" {
  count              = var.enable_invocation_logging ? 1 : 0
  name               = "${var.name_prefix}-bedrock-logging"
  assume_role_policy = data.aws_iam_policy_document.bedrock_logging_trust[0].json
  tags               = local.base_tags
}

data "aws_iam_policy_document" "bedrock_logging" {
  count = var.enable_invocation_logging ? 1 : 0

  statement {
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.bedrock_invocations[0].arn}:*"]
  }
}

resource "aws_iam_role_policy" "bedrock_logging" {
  count  = var.enable_invocation_logging ? 1 : 0
  name   = "${var.name_prefix}-bedrock-logging"
  role   = aws_iam_role.bedrock_logging[0].id
  policy = data.aws_iam_policy_document.bedrock_logging[0].json
}

resource "aws_bedrock_model_invocation_logging_configuration" "this" {
  count = var.enable_invocation_logging ? 1 : 0

  logging_config {
    embedding_data_delivery_enabled = true
    image_data_delivery_enabled     = false
    text_data_delivery_enabled      = true
    video_data_delivery_enabled     = false

    cloudwatch_config {
      log_group_name = aws_cloudwatch_log_group.bedrock_invocations[0].name
      role_arn       = aws_iam_role.bedrock_logging[0].arn
    }
  }

  depends_on = [aws_iam_role_policy.bedrock_logging]
}
