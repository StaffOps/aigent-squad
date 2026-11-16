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
| An LLM: AWS credentials with Bedrock access (default) **or** any OpenAI-compatible API (`LLM_PROVIDER=openai`, below) | Every real agent answer is a live LLM call — no offline/fixture mode yet |
| Bedrock model access, `us-east-1` | Console → Model access → enable the models below |

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
