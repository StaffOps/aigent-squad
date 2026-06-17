terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

# =====================================================================
# Application Inference Profiles (AIP) — one per model.
#
# An AIP wraps a model and carries cost allocation tags into every
# invocation's billing record. The app invokes the AIP ARN instead of
# the raw model id, so Cost Explorer / CUR can break Bedrock spend down
# by CostProject/CostScope/Environment/CostCenter.
#
# Per-agent cost is NOT done via per-agent AIPs (see spec 27 design):
# it is derived from the per-agent token metric (showback), keeping the
# number of AIPs minimal (one per model).
#
# `copy_from` points at the SYSTEM inference profile (the "us." one),
# which is required because Claude Sonnet 4.5 has no on-demand support
# on the bare foundation-model ARN and the "us." profile adds
# cross-region routing.
#
# Cost allocation tags (CostProject/CostScope/Environment/CostCenter) are
# applied centrally via the provider's `default_tags` — not here. Pass
# extra per-AIP tags via var.tags if needed.
# =====================================================================

resource "aws_bedrock_inference_profile" "this" {
  for_each = var.models

  name        = "${var.name_prefix}-${each.key}"
  description = "Cost-attribution AIP for ${each.key}"

  model_source {
    copy_from = each.value # ARN of the system-defined "us." inference profile
  }

  tags = merge(var.tags, {
    Module = "bedrock-aip"
    Model  = each.key
  })
}
