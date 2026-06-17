# Prerequisites

## Required

| Requirement | Purpose |
|-------------|---------|
| Docker + Docker Compose | Build and run all services |
| ssh-agent with GitHub key | Private `otel-helper` repo cloned during `docker build` |
| AWS Bedrock model access | Claude Sonnet + Titan Embeddings v2 in `us-east-1` |

### Enable Bedrock models

1. Go to https://console.aws.amazon.com/bedrock/home?region=us-east-1#/modelaccess
2. Enable: `anthropic.claude-3-5-sonnet-20241022-v2:0` and `amazon.titan-embed-text-v2:0`

## Optional (for full functionality)

| Requirement | Purpose | Without it |
|-------------|---------|------------|
| AWS credentials (`~/.aws/`) | AWS/FinOps agent datasources | Agents degrade gracefully (return empty data) |
| Kubernetes config (`~/.kube/config`) | K8s agent datasource | K8s agent returns empty data |

## Production-only

| Requirement | Purpose |
|-------------|---------|
| EKS cluster with IRSA | Pod-level AWS auth (no access keys) |
| Helm 3.x | Chart deployment |
| ECR/Harbor registry | Image storage |
| AWS Secrets Manager + ESO | Secret injection |
