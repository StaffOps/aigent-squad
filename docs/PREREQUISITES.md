# Prerequisites

## Required

| Requirement | Purpose |
|-------------|---------|
| Docker + Docker Compose | Build and run all services |

That's it for `make lint` / `make test` — the full test suite (≥90% coverage
gate) runs entirely in Docker with no AWS credentials and no live Bedrock
calls (`otel-helper` is a public repo as of 2026-07-14; no build-time
credential needed either). See `CONTRIBUTING.md`.

## To run a live query (`make up` + `make smoke`, or any real agent answer)

| Requirement | Purpose |
|-------------|---------|
| AWS credentials with Bedrock access (`~/.aws/` or env vars) | Every agent answer is a real Bedrock call — there's no offline/fixture mode today (scoped, not built — `specs/BACKLOG.md` B-29) |
| Bedrock model access enabled, `us-east-1` | Console → **Model access** → enable the Anthropic models below |

### Enable Bedrock models

1. Go to https://console.aws.amazon.com/bedrock/home?region=us-east-1#/modelaccess
2. Enable the models `src/core/config.py` defaults to (override via
   `BEDROCK_MODEL_ID` / `BEDROCK_CLASSIFIER_MODEL_ID` if you use different
   ones): an inference-profile Claude Sonnet (agents/synthesis) and Claude
   Haiku (classifier). Both need an **inference profile** (`us.` prefix),
   not the raw on-demand model id — see `AGENTS.md`'s playbook table.

This is intentionally the ONLY AWS requirement for a local demo — **no
Terraform, no IRSA, no EKS, no Bedrock Guardrail setup**. Local compose
defaults `GUARDRAIL_ENABLED=false` and disables the optional knowledge base
(Postgres/pgvector, Titan Embeddings), so nothing beyond a Bedrock-capable
credential is needed to see real agent answers.

## Optional (for full functionality)

| Requirement | Purpose | Without it |
|-------------|---------|------------|
| Kubernetes config (`~/.kube/config`) | K8s agent datasource | K8s agent returns empty data |
| Postgres + pgvector, Titan Embeddings v2 access | Knowledge base / RAG (spec 21) | KB disabled by default; agents work without it |

## Production-only

| Requirement | Purpose |
|-------------|---------|
| EKS cluster with IRSA | Pod-level AWS auth (no access keys) |
| Helm 3.x | Chart deployment |
| A container registry (Docker Hub, ECR, Harbor, …) | Image storage |
| AWS Secrets Manager + ESO | Secret injection |
