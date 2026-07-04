---
name: verify
description: Bring the local aigent-squad stack up and prove it works end-to-end (gateway → supervisor → Bedrock). Use after any change to src/, agents/ or docker-compose.
---

# Verify the squad locally

1. `make up` — starts the two-tier stack, waits for gateway `/ready` (60s max).
2. `make smoke` — health + one real `/query` (Bedrock round-trip) + `/v1/models`.
3. If code changed, also: `make test` (full gate) and `make lint`.

## When it fails

Every smoke failure prints a playbook pointer. Quick map (full table:
AGENTS.md → Playbook):

- **gateway not ready** → `docker compose logs gateway supervisor`; port busy? `lsof -i :8000`
- **401** → edge token (`X-Internal-Token` vs `INTERNAL_API_TOKEN`) — NOT the supervisor token
- **403** → fail-closed security (guardrail/scanner). Local compose defaults `GUARDRAIL_ENABLED=false`; a 403 locally means an env override or a scanner block
- **503** → supervisor down or pool full — logs first
- **Bedrock errors** → AWS creds (`aws sts get-caller-identity`) + model id must be an inference profile (`us.` prefix)

## Definition of verified

`make smoke` green AND (if code changed) `make test` green. A stubbed test run
prints a warning — then CI is the real verdict: `gh run list -L 3`.
