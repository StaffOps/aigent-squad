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
| An LLM: AWS credentials with Bedrock access (`~/.aws/` or env vars, default) **or** any OpenAI-compatible API (`LLM_PROVIDER=openai`, below) | Every agent answer is a real LLM call — there's no offline/fixture mode today (scoped, not built — `specs/BACKLOG.md` B-29) |
| Bedrock model access enabled, `us-east-1` | Console → **Model access** → enable the Anthropic models below |

### Or: any OpenAI-compatible API instead of Bedrock (spec 28)

Set `LLM_PROVIDER=openai` and point it at any `POST /chat/completions` endpoint
(OpenAI, an OpenAI-compatible Azure endpoint, vLLM, Ollama, OpenRouter, a LiteLLM
proxy):

```bash
LLM_PROVIDER=openai
OPENAI_BASE_URL=http://ollama:11434/v1      # no trailing /chat/completions
OPENAI_API_KEY_FILE=/etc/secrets/openai-key # or OPENAI_API_KEY; any non-empty value for keyless servers
LLM_MODEL_ID=llama3.1:70b                   # required; classifier/synthesis fall back to it
# optional: LLM_CLASSIFIER_MODEL_ID, LLM_SYNTHESIS_MODEL_ID, LLM_PRICE_INPUT_PER_1M/OUTPUT_PER_1M
```

The supervisor refuses to start if the base URL, key or model is missing.
**The Bedrock Guardrail (L1) is switched off automatically** under this provider
(startup WARNING, `aigent.llm.guardrail_l1_active=0`, `/ready` detail) — see
`docs/SECURITY.md`. No Bedrock model access is needed; DynamoDB (history) is
still used (`dynamodb-local` in compose).

### Enable Bedrock models (default provider)

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
| A container registry (GHCR is the default; Harbor for dev) | Image storage |
| AWS Secrets Manager + ESO | Secret injection |
