---
name: new-agent
description: Add a new specialist agent to aigent-squad — config only, zero code. Use when asked to create/scaffold an agent.
---

# Add a new agent (config-only)

Canonical guide: `docs/HOW-TO-NEW-AGENT.md` · schema reference: AGENTS.md →
"Agent config schema" · full schema: `src/core/agent_config.py`.

1. `mkdir agents/<name>`
2. `agents/<name>/agent.yaml` — required: `name`, `description` (the classifier
   routes on it — write it for routing), `domain`, `capabilities`,
   `datasources`. Recommended: `routing_keywords`, `cache.ttl`, `read_only: true`.
3. `agents/<name>/prompt.md` — system prompt (English).
4. Restart to auto-discover: `docker compose restart supervisor`
5. Verify: agent appears in `curl -s -H 'X-Internal-Token: dev-secret-token' \
   http://localhost:8000/v1/models` as `aigent-squad-<name>`, and a routed query
   answers: `make smoke` variant or a direct `/v1/chat/completions` with
   `model: aigent-squad-<name>`.

Notes that bite:
- Invalid YAML = supervisor fails AT STARTUP (Pydantic, fail-fast) — check
  `docker compose logs supervisor` first.
- Datasource types available: `boto3`, `kubernetes`, `http`, `athena`, `mcp`
  (fail-closed tool allowlist for mcp).
- When spec 35 lands: a new agent needs its golden set (`evals/golden/<name>.yaml`).
