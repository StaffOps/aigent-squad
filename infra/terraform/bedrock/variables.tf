variable "name_prefix" {
  description = "Prefix for resource names (e.g. 'aigent-squad')"
  type        = string
}

variable "region" {
  description = "AWS region (Bedrock available in us-east-1, us-west-2, eu-central-1, etc)"
  type        = string
  default     = "us-east-1"
}

variable "tags" {
  description = "Tags to apply to all taggable resources"
  type        = map(string)
  default     = {}
}

# ----- VPC endpoints -----

variable "vpc_id" {
  description = "VPC ID where the VPC endpoints will be created (private Bedrock access from EKS pods)"
  type        = string
}

variable "subnet_ids" {
  description = "Private subnet IDs for the VPC endpoints (typically the same subnets as EKS workers)"
  type        = list(string)
}

variable "security_group_ids" {
  description = "Security group IDs to attach to the VPC endpoints. Must allow ingress 443 from EKS worker SGs."
  type        = list(string)
}

# ----- Logging (disabled by default) -----

variable "enable_invocation_logging" {
  description = "Enable Bedrock invocation logging to CloudWatch (cost: log ingestion + Bedrock invocation logging fee)"
  type        = bool
  default     = false
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days (only used if enable_invocation_logging=true)"
  type        = number
  default     = 30
}
