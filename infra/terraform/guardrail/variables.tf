variable "name_prefix" {
  description = "Guardrail name / resource prefix (e.g. 'aigent-squad')"
  type        = string
  default     = "aigent-squad"
}

variable "tags" {
  description = "Tags to apply to all taggable resources"
  type        = map(string)
  default     = {}
}

# ----- Prompt-attack filter (the primary anti-injection control) -----

variable "prompt_attack_strength" {
  description = "Strength of the PROMPT_ATTACK input filter (NONE/LOW/MEDIUM/HIGH). HIGH = most aggressive jailbreak/injection detection."
  type        = string
  default     = "HIGH"

  validation {
    condition     = contains(["NONE", "LOW", "MEDIUM", "HIGH"], var.prompt_attack_strength)
    error_message = "prompt_attack_strength must be one of NONE, LOW, MEDIUM, HIGH."
  }
}

# ----- Harmful-content filters (secondary, both directions) -----

variable "content_filter_categories" {
  description = "Content filter categories applied input+output (e.g. HATE, INSULTS, SEXUAL, VIOLENCE, MISCONDUCT)."
  type        = list(string)
  default     = ["HATE", "INSULTS", "SEXUAL", "VIOLENCE", "MISCONDUCT"]
}

variable "content_filter_strength" {
  description = "Strength for the harmful-content filters (NONE/LOW/MEDIUM/HIGH)."
  type        = string
  default     = "MEDIUM"

  validation {
    condition     = contains(["NONE", "LOW", "MEDIUM", "HIGH"], var.content_filter_strength)
    error_message = "content_filter_strength must be one of NONE, LOW, MEDIUM, HIGH."
  }
}

# ----- PII (exfiltration mitigation) -----

variable "pii_entities" {
  description = "PII entity types the guardrail detects (AWS Bedrock PII entity type names)."
  type        = list(string)
  default = [
    "EMAIL",
    "PHONE",
    "AWS_ACCESS_KEY",
    "AWS_SECRET_KEY",
    "PASSWORD",
    "USERNAME",
    "IP_ADDRESS",
  ]
}

variable "pii_action" {
  description = "Action on detected PII: BLOCK (refuse) or ANONYMIZE (mask). BLOCK is fail-closed-aligned."
  type        = string
  default     = "BLOCK"

  validation {
    condition     = contains(["BLOCK", "ANONYMIZE"], var.pii_action)
    error_message = "pii_action must be BLOCK or ANONYMIZE."
  }
}

# ----- Denied topics (optional, operator-defined) -----

variable "denied_topics" {
  description = "Off-limits topics. Each: name, definition, and example phrases."
  type = list(object({
    name       = string
    definition = string
    examples   = list(string)
  }))
  default = []
}

# ----- Refusal messages (returned by AWS when content is blocked) -----

variable "blocked_input_messaging" {
  description = "Message returned when the INPUT is blocked by the guardrail."
  type        = string
  default     = "This request was blocked by the security guardrail."
}

variable "blocked_outputs_messaging" {
  description = "Message returned when the OUTPUT is blocked by the guardrail."
  type        = string
  default     = "The response was blocked by the security guardrail."
}
