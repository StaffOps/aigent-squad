output "table_name" {
  description = "Name of the sessions table (pass to env DYNAMODB_SESSIONS_TABLE)"
  value       = aws_dynamodb_table.sessions.name
}

output "table_arn" {
  description = "ARN of the sessions table (used by the IAM policy)"
  value       = aws_dynamodb_table.sessions.arn
}
