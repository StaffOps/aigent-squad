output "inference_profile_arns" {
  description = "Map of model_key => Application Inference Profile ARN. Use the ARN as BEDROCK_MODEL_ID in the app."
  value       = { for k, v in aws_bedrock_inference_profile.this : k => v.inference_profile_arn }
}

output "inference_profile_ids" {
  description = "Map of model_key => AIP ID"
  value       = { for k, v in aws_bedrock_inference_profile.this : k => v.id }
}
