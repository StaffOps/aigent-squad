# Prerequisites

## Required

| Requirement | Purpose |
|-------------|---------|
| Docker + Docker Compose | Build and run all services locally |
| SSH key loaded in `ssh-agent` | Private `otel-helper` dep cloned during `docker build` |
| AWS Bedrock model access | Claude Sonnet + Titan Embeddings v2 in `us-east-1` |

### Enable Bedrock models

1. Open the [Bedrock Model Access console](https://console.aws.amazon.com/bedrock/home?region=us-east-1#/modelaccess)
2. Enable:
    - `anthropic.claude-sonnet-4-5-20250929-v1:0` (or your preferred Claude model)
    - `amazon.titan-embed-text-v2:0`

---

## Optional (for full functionality)

| Requirement | Purpose | Without it |
|-------------|---------|------------|
| AWS credentials (`~/.aws/`) | AWS / FinOps agent datasources | Agents degrade gracefully — return empty data |
| Kubernetes config (`~/.kube/config`) | K8s agent datasource | K8s agent returns empty data |

---

## Production only

| Requirement | Purpose |
|-------------|---------|
| EKS cluster with IRSA | Pod-level AWS auth (no static access keys) |
| Helm 3.x | Chart deployment |
| AWS Secrets Manager + ESO | Secret injection via `ExternalSecret` |
| ElastiCache Redis | Agent data cache (shared across pods) |
| DynamoDB table | Conversation history (`agent-sessions`) |

[Installation →](installation.md){ .md-button .md-button--primary }
