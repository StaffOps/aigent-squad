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
    Module    = "dynamodb"
    ManagedBy = "terraform"
    Component = var.name_prefix
  })
}

# =====================================================================
# Conversation/session history table
#
# Access pattern (see src/core/state_store.py::ChatStorage):
#   - pk = "<user_id>#<session_id>"   (HASH)
#   - sk = "<agent_id>#<iso_timestamp>" (RANGE)
#   - Query: pk = :pk AND begins_with(sk, :agent)
#   - TTL on attribute "ttl" (24h expiry, set by the app)
#
# PAY_PER_REQUEST: traffic is spiky and low-volume (one item per chat
# turn). On-demand avoids capacity planning and idle cost.
# =====================================================================

resource "aws_dynamodb_table" "sessions" {
  name         = var.table_name
  billing_mode = "PAY_PER_REQUEST"

  hash_key  = "pk"
  range_key = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = var.enable_point_in_time_recovery
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = var.kms_key_arn # null => AWS-owned key (no extra cost)
  }

  deletion_protection_enabled = var.enable_deletion_protection

  tags = merge(local.base_tags, {
    Name = var.table_name
  })
}
