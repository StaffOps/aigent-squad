output "role_arn" {
  description = "ARN of the IRSA role to annotate on the Kubernetes ServiceAccount"
  value       = aws_iam_role.this.arn
}

output "role_name" {
  description = "Name of the IRSA role"
  value       = aws_iam_role.this.name
}

output "service_account_annotation" {
  description = "Ready-to-paste annotation for the K8s ServiceAccount"
  value       = "eks.amazonaws.com/role-arn: ${aws_iam_role.this.arn}"
}
