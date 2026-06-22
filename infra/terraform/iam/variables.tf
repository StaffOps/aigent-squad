variable "name_prefix" {
  description = "Prefix for resource names (e.g. 'aigent-squad')"
  type        = string
}

variable "region" {
  description = "AWS region (used to build Athena/Glue ARNs when FinOps is enabled)"
  type        = string
  default     = "us-east-1"
}

variable "account_id" {
  description = "AWS account ID (used to build Athena/Glue ARNs). Pass data.aws_caller_identity.current.account_id."
  type        = string
}

variable "tags" {
  description = "Tags applied to all taggable resources"
  type        = map(string)
  default     = {}
}

# ----- IRSA trust (EKS pod identity) -----

variable "eks_oidc_provider_arn" {
  description = "OIDC provider ARN for the EKS cluster"
  type        = string
}

variable "eks_oidc_provider_url" {
  description = "OIDC provider URL without https:// prefix (e.g. oidc.eks.us-east-1.amazonaws.com/id/XXX)"
  type        = string
}

variable "k8s_namespace" {
  description = "Kubernetes namespace where the supervisor pod runs"
  type        = string
  default     = "aigent-squad"
}

variable "k8s_service_account" {
  description = "Kubernetes ServiceAccount name (must match Helm chart serviceAccount.name)"
  type        = string
  default     = "aigent-squad"
}

# ----- Bedrock -----

variable "allowed_model_arns" {
  description = "Bedrock ARNs the role can invoke. Claude Sonnet 4.5 requires an INFERENCE_PROFILE, so the app invokes either the system 'us.' profile OR an Application Inference Profile (AIP, account-scoped, used for cost attribution — spec 27). Both, plus the underlying foundation models the profile routes to, must be allowed."
  type        = list(string)
  default = [
    # Application Inference Profiles (cost attribution — spec 27; account-scoped ARN)
    "arn:aws:bedrock:*:*:application-inference-profile/*",
    # System inference profiles (the 'us.' cross-region profiles)
    "arn:aws:bedrock:*:*:inference-profile/us.anthropic.claude-*",
    "arn:aws:bedrock:*:*:inference-profile/us.amazon.titan-*",
    # Underlying foundation models the profile routes to (cross-region: us-east-1/us-east-2/us-west-2)
    "arn:aws:bedrock:*::foundation-model/anthropic.claude-*",
    "arn:aws:bedrock:*::foundation-model/amazon.titan-*",
  ]
}

# ----- DynamoDB -----

variable "sessions_table_arn" {
  description = "ARN of the DynamoDB sessions table (output of the dynamodb module)"
  type        = string
}

# ----- FinOps via Athena/CUR (optional, disabled by default) -----

variable "enable_athena_finops" {
  description = "Enable Athena/Glue/S3 permissions for CUR-based FinOps. Keep false until a CUR Athena target exists (CUR may live in the payer account — see main.tf)."
  type        = bool
  default     = false
}

variable "athena_workgroup" {
  description = "Athena workgroup name (only used if enable_athena_finops=true)"
  type        = string
  default     = "primary"
}

variable "athena_database" {
  description = "Glue/Athena database name for CUR (only used if enable_athena_finops=true)"
  type        = string
  default     = ""
}

variable "cur_s3_bucket_arns" {
  description = "S3 bucket ARNs holding the CUR data, e.g. [\"arn:aws:s3:::my-cur-bucket\", \"arn:aws:s3:::my-cur-bucket/*\"] (only used if enable_athena_finops=true)"
  type        = list(string)
  default     = []
}

variable "athena_results_bucket_arn" {
  description = "S3 bucket ARN for Athena query results (only used if enable_athena_finops=true)"
  type        = string
  default     = ""
}
