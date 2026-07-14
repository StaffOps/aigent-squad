#!/usr/bin/env bash
# Quality eval T2 runner (spec 35 T6): golden sets against the real local
# stack + an LLM judge (Haiku, temp 0). Real Bedrock cost — never runs
# implicitly, only via `make eval`. Requires the local stack up (`make up`)
# and real AWS credentials for Bedrock (same requirement as `make smoke`).
set -euo pipefail

cd "$(dirname "$0")/.."

if ! curl -sf http://localhost:8000/ready >/dev/null 2>&1; then
  echo "❌ gateway not ready at http://localhost:8000/ready"
  echo "   ↳ stack up? → make up"
  exit 1
fi

echo "Running spec 35 T2 golden-set eval (real Bedrock calls, ~\$1-3)…"

docker run --rm \
  -v "${HOME}/.aws:/home/appuser/.aws" \
  -v "$(pwd)/src:/app/src:ro" \
  -v "$(pwd)/agents:/app/agents:ro" \
  -v "$(pwd)/evals:/app/evals" \
  -e AWS_REGION="${AWS_REGION:-us-east-1}" \
  -e AWS_DEFAULT_REGION="${AWS_REGION:-us-east-1}" \
  -e BEDROCK_MODEL_ID="${BEDROCK_MODEL_ID:-us.anthropic.claude-sonnet-4-5-20250929-v1:0}" \
  -e BEDROCK_CLASSIFIER_MODEL_ID="${BEDROCK_CLASSIFIER_MODEL_ID:-us.anthropic.claude-haiku-4-5-20251001-v1:0}" \
  -e INTERNAL_API_TOKEN="${INTERNAL_API_TOKEN:-dev-secret-token}" \
  -e SUPERVISOR_INTERNAL_TOKEN="${SUPERVISOR_INTERNAL_TOKEN:-dev-internal-token}" \
  -e GUARDRAIL_ENABLED="${GUARDRAIL_ENABLED:-false}" \
  -e GATEWAY_URL="http://gateway:8000" \
  -e OTEL_EXPORTER_OTLP_ENDPOINT="http://otel-collector:4317" \
  -e REDIS_HOST=redis -e REDIS_PORT=6379 -e REDIS_PASSWORD="${REDIS_PASSWORD:-changeme}" \
  --network aigent-squad_default \
  -w /app \
  aigent-squad-supervisor:latest \
  python3 evals/runner.py
