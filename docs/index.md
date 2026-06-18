# Aigent Squad

**Multi-agent platform for AWS and Kubernetes operations.**

One supervisor, five specialist agents, zero code to add a new one.
Config-driven, read-only by default, fully observable.

---

## What it does

Aigent Squad receives operational questions in natural language and routes them to the right specialist agent. Each agent collects read-only context from its data sources and synthesizes a structured answer using Claude on AWS Bedrock.

```
Your question
     │
     ▼
Supervisor (classifier + orchestrator)
     │
     ├──▶ AWS Agent       → EC2, RDS, S3, IAM, Cost Explorer
     ├──▶ Kubernetes Agent → Pods, deployments, events, metrics
     ├──▶ FinOps Agent    → Cost analysis, savings plans
     ├──▶ DevOps Agent    → CI/CD, GitLab, pipelines
     └──▶ Observability   → Metrics, logs, traces, alerts
```

---

## Key properties

| Property | Detail |
|----------|--------|
| **Read-only** | Agents never execute mutations — analysis and recommendations only |
| **Config-driven** | Add an agent with 2 files (`agent.yaml` + `prompt.md`), no code |
| **Single image** | Supervisor runs all agents in-process; one container to deploy |
| **Cost attributed** | Per-agent token counting via Bedrock inference profiles |
| **Observable** | OTel traces, RED metrics, and structured logs on every component |
| **Conversation history** | DynamoDB with 24h TTL per user/session |

---

## Quick install

```bash
helm repo add staffops https://staffops.github.io/helm-charts/
helm repo update

helm install aigent-squad staffops/aigent-squad \
  --namespace aigent-squad --create-namespace \
  --set redis.host=<your-elasticache-endpoint>
```

[Getting Started →](getting-started/prerequisites.md){ .md-button .md-button--primary }
[Architecture →](architecture.md){ .md-button }

---

## Source

[github.com/StaffOps/staffops-aigent-squad](https://github.com/StaffOps/staffops-aigent-squad) — Apache 2.0
