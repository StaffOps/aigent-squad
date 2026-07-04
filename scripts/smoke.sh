#!/usr/bin/env bash
# Local smoke test (spec 36 T3): gateway health + one real query + OpenAI bridge.
# Requires the local stack up (make up) and AWS credentials for Bedrock.
# Failure messages map to the AGENTS.md playbook.
set -euo pipefail

GATEWAY="${GATEWAY_URL:-http://localhost:8000}"
TOKEN="${INTERNAL_API_TOKEN:-dev-secret-token}"

fail() { echo "❌ $1"; echo "   ↳ playbook: $2 (AGENTS.md → Playbook)"; exit 1; }

echo "1/3 gateway /ready…"
curl -sf "${GATEWAY}/ready" >/dev/null \
  || fail "gateway not ready at ${GATEWAY}/ready" "stack up? → make up; port busy? → lsof -i :8000"
echo "   ✅ ready"

echo "2/3 real query via /query (Bedrock round-trip)…"
HTTP=$(curl -s -o /tmp/smoke-query.json -w "%{http_code}" -X POST "${GATEWAY}/query" \
  -H 'Content-Type: application/json' -H "X-Internal-Token: ${TOKEN}" \
  -d '{"user_input":"How many EC2 instances are running?","user_id":"smoke","session_id":"smoke-1"}')
case "${HTTP}" in
  200) echo "   ✅ 200 — agent: $(grep -o '"agent"[^,}]*' /tmp/smoke-query.json | head -1)";;
  401) fail "401 unauthorized" "token mismatch — X-Internal-Token vs INTERNAL_API_TOKEN (edge) — NOT the supervisor token";;
  403) fail "403 blocked" "fail-closed security: guardrail/scanner. Local: GUARDRAIL_ENABLED=false (compose default) — did you override it without a provisioned GUARDRAIL_ID?";;
  503) fail "503 unavailable" "supervisor unreachable or pool full — docker compose logs supervisor";;
  *)   fail "unexpected HTTP ${HTTP} (body: /tmp/smoke-query.json)" "docker compose logs gateway supervisor";;
esac

echo "3/3 OpenAI bridge /v1/models…"
curl -sf -H "X-Internal-Token: ${TOKEN}" "${GATEWAY}/v1/models" | grep -q '"aigent-squad"' \
  || fail "/v1/models missing aigent-squad model" "docker compose logs gateway"
echo "   ✅ bridge OK"

echo ""
echo "✅ smoke passed (${GATEWAY})"
