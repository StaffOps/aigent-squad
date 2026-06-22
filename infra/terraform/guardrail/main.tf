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
    Module    = "guardrail"
    ManagedBy = "terraform"
    Component = var.name_prefix
  })
}

# =====================================================================
# Bedrock Guardrail — primary anti-prompt-injection layer (spec 14, L1)
#
# Evaluated independently of the agent's prompt via the apply_guardrail
# API (see src/core/guardrail.py). An injection that fools the LLM does
# not fool the guardrail — distinct evaluations = real defense-in-depth.
#
# prompt-attack detection is natively multi-language (meets the
# "any language" requirement without training our own classifier).
# =====================================================================

resource "aws_bedrock_guardrail" "this" {
  name                      = var.name_prefix
  description               = "Anti-prompt-injection defense for AIgent-squad (spec 14, read-only phase)"
  blocked_input_messaging   = var.blocked_input_messaging
  blocked_outputs_messaging = var.blocked_outputs_messaging

  # ----- Content filters (prompt-attack is the primary one) -----
  # PROMPT_ATTACK is INPUT-only by AWS constraint: output_strength MUST be NONE.
  content_policy_config {
    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = var.prompt_attack_strength
      output_strength = "NONE"
    }

    # Defense-in-depth content categories (harmful content in either direction).
    dynamic "filters_config" {
      for_each = var.content_filter_categories
      content {
        type            = filters_config.value
        input_strength  = var.content_filter_strength
        output_strength = var.content_filter_strength
      }
    }
  }

  # ----- PII detection (exfiltration / info-disclosure mitigation) -----
  dynamic "sensitive_information_policy_config" {
    for_each = length(var.pii_entities) > 0 ? [1] : []
    content {
      dynamic "pii_entities_config" {
        for_each = var.pii_entities
        content {
          type   = pii_entities_config.value
          action = var.pii_action
        }
      }
    }
  }

  # ----- Denied topics (operator-defined off-limits subjects) -----
  dynamic "topic_policy_config" {
    for_each = length(var.denied_topics) > 0 ? [1] : []
    content {
      dynamic "topics_config" {
        for_each = var.denied_topics
        content {
          name       = topics_config.value.name
          definition = topics_config.value.definition
          examples   = topics_config.value.examples
          type       = "DENY"
        }
      }
    }
  }

  tags = local.base_tags
}

# Immutable published version the application pins to (guardrail_version).
# DRAFT is the mutable working copy; the app must reference a numbered version.
resource "aws_bedrock_guardrail_version" "this" {
  guardrail_arn = aws_bedrock_guardrail.this.guardrail_arn
  description   = "Published version consumed by the application"
}
