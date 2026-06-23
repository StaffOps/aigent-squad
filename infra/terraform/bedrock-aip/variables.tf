variable "name_prefix" {
  description = "Prefix for AIP names (e.g. 'aigent-squad')"
  type        = string
  default     = "aigent-squad"
}

variable "models" {
  description = <<-EOT
    Map of model_key => source ARN to create one AIP per model. The source
    ARN MUST be a system-defined inference profile (the 'us.' one), e.g.:
    {
      sonnet45 = "arn:aws:bedrock:us-east-1:ACCOUNT:inference-profile/us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    }
    Discover system profiles with: aws bedrock list-inference-profiles.
  EOT
  type        = map(string)
}

# ----- FinOps cost allocation tags -----
#
# Cost tags (CostProject/CostScope/Environment/CostCenter) are applied
# centrally via the AWS provider's `default_tags` block, so they are NOT
# module inputs. Use var.tags only for extra per-AIP tags.

variable "tags" {
  description = "Additional tags merged into every AIP (cost tags come from provider default_tags)"
  type        = map(string)
  default     = {}
}
