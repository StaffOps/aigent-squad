# Prerequisites

## Required

| Requirement | Purpose |
|-------------|---------|
| Docker + Docker Compose | Build and run all services locally |

That covers `make lint` / `make test` in full — the test suite (≥90%
coverage gate) runs entirely in Docker, no AWS credentials, no live Bedrock
calls. `otel-helper` is a public repo (since 2026-07-14), so no build-time
credential is needed either.

## To run a live query

| Requirement | Purpose |
|-------------|---------|
| AWS credentials with Bedrock access (`~/.aws/` or env vars) | Every real agent answer is a live Bedrock call — no offline/fixture mode yet |
| Bedrock model access, `us-east-1` | Console → Model access → enable the models below |

### Enable Bedrock models

1. Open the [Bedrock Model Access console](https://console.aws.amazon.com/bedrock/home?region=us-east-1#/modelaccess)
2. Enable the models `src/core/config.py` defaults to (or your own, via
   `BEDROCK_MODEL_ID` / `BEDROCK_CLASSIFIER_MODEL_ID`): a Claude Sonnet
   inference profile for agents/synthesis, Claude Haiku for the classifier.
   Both need an **inference profile** (`us.` prefix) — a raw on-demand
   model id fails at call time.

This is the ONLY AWS requirement for a local demo — **no Terraform, no
IRSA, no EKS, no Guardrail setup**. Local compose defaults
`GUARDRAIL_ENABLED=false` and the knowledge base (Postgres/pgvector, Titan
Embeddings) is disabled by default, so a Bedrock-capable credential is all
you need to see real agent answers.

---

## Optional (for full functionality)

| Requirement | Purpose | Without it |
|-------------|---------|------------|
| Kubernetes config (`~/.kube/config`) | K8s agent datasource | K8s agent returns empty data |
| Postgres + pgvector, Titan Embeddings v2 access | Knowledge base / RAG (spec 21) | KB disabled by default; agents work without it |

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
