# The application pins to these two values via env:
#   GUARDRAIL_ID      ← guardrail_id
#   GUARDRAIL_VERSION ← guardrail_version
# See src/core/config.py (Settings.guardrail_id / guardrail_version).

output "guardrail_id" {
  description = "Guardrail identifier (set as GUARDRAIL_ID in the app env)"
  value       = aws_bedrock_guardrail.this.guardrail_id
}

output "guardrail_arn" {
  description = "Guardrail ARN (use to scope the IRSA invoke policy)"
  value       = aws_bedrock_guardrail.this.guardrail_arn
}

output "guardrail_version" {
  description = "Published immutable version (set as GUARDRAIL_VERSION in the app env)"
  value       = aws_bedrock_guardrail_version.this.version
}
