output "vpc_endpoint_runtime_id" {
  description = "VPC endpoint ID for bedrock-runtime"
  value       = aws_vpc_endpoint.bedrock_runtime.id
}

output "vpc_endpoint_runtime_dns" {
  description = "Private DNS names for bedrock-runtime endpoint"
  value       = aws_vpc_endpoint.bedrock_runtime.dns_entry[*].dns_name
}

output "vpc_endpoint_control_id" {
  description = "VPC endpoint ID for bedrock (control plane)"
  value       = aws_vpc_endpoint.bedrock.id
}

output "log_group_arn" {
  description = "CloudWatch log group ARN (only if enable_invocation_logging=true)"
  value       = var.enable_invocation_logging ? aws_cloudwatch_log_group.bedrock_invocations[0].arn : null
}
