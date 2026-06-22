variable "name_prefix" {
  description = "Prefix for resource naming/tags (e.g. 'aigent-squad')"
  type        = string
}

variable "table_name" {
  description = "DynamoDB table name. Must match env DYNAMODB_SESSIONS_TABLE."
  type        = string
  default     = "agent-sessions"
}

variable "enable_point_in_time_recovery" {
  description = "Enable PITR (continuous backups). Adds cost (~$0.20/GB/mo of backup). Recommended for PRD."
  type        = bool
  default     = true
}

variable "enable_deletion_protection" {
  description = "Prevent accidental table deletion via API/console/terraform destroy."
  type        = bool
  default     = false
}

variable "kms_key_arn" {
  description = "Customer-managed KMS key ARN for SSE. Leave null to use the AWS-owned key (no extra cost)."
  type        = string
  default     = null
}

variable "tags" {
  description = "Tags applied to all taggable resources"
  type        = map(string)
  default     = {}
}
