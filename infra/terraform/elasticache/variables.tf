variable "name_prefix" {
  description = "Prefix for resource naming/tags (e.g. 'aigent-squad')"
  type        = string
}

variable "vpc_id" {
  description = "VPC where the cache subnet group + security group live"
  type        = string
}

variable "subnet_ids" {
  description = "Private subnet IDs for the cache subnet group"
  type        = list(string)
}

variable "eks_worker_security_group_id" {
  description = "Security group of the EKS workers allowed to reach the cache"
  type        = string
}

variable "node_type" {
  description = "Cache node type. t4g.micro is the minimal footprint for dev/validation; use a larger type + replication group for PRD."
  type        = string
  default     = "cache.t4g.micro"
}

variable "engine_version" {
  description = "Valkey engine version."
  type        = string
  default     = "8.0"
}

variable "parameter_group_name" {
  description = "Cache parameter group. Defaults to the Valkey 8 family default."
  type        = string
  default     = "default.valkey8"
}

variable "port" {
  description = "Cache port (app default REDIS_PORT is 6379)"
  type        = number
  default     = 6379
}

# ----- Security (deferred phase — default OFF so the module validates and a
# minimal cluster stands up cheaply). Enable together with an ExternalSecret
# providing REDIS_PASSWORD (auth token) and set REDIS_SSL=true in the app. -----
variable "transit_encryption_enabled" {
  description = "Enable in-transit TLS. Turn on with an AUTH token + ExternalSecret (security phase). App must set REDIS_SSL=true."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Tags applied to all taggable resources"
  type        = map(string)
  default     = {}
}
