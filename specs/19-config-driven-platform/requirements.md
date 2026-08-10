---
spec: 19-config-driven-platform
status: superseded
completed: null
superseded_by: "22-agent-capability-manifest"
depends_on: []
deferred: []
---

# Feature: Config-Driven Platform

**Spec**: `19-config-driven-platform`
**Severity**: 🟠 High (product prerequisite)
**Origin**: user requirement (becomes a product → configuration via config file + env); hardcoded findings (`AGENT_URLS`, `mcp_servers`, `PROMETHEUS_URL`, GitLab groups)
**Depends on**: — (can start early, in parallel)

Today the configuration is scattered and partly hardcoded: `AGENT_URLS` in `supervisor/agent.py`, `mcp_servers` in `config.py`, `PROMETHEUS_URL` in the observability agent, GitLab groups ("Company") in `gitlab_client`. To become a product, **all** configuration must come from a **declarative config file + env var override**, with no hardcoded values in the code.

## User Stories

WHEN the operator defines the agents (endpoint, model, datasources, enable/disable) THEN the system SHALL read that from a **config file** (YAML), not from constants in the code.

WHEN a corresponding env var exists THEN it SHALL **override** the config file value (precedence: env > file > default).

WHEN a secret is needed (tokens, passwords) THEN it SHALL come from an env var or mounted file (file-mounted), **never** from the versioned config file.

WHEN the supervisor needs an agent's URL THEN it SHALL come from the agent registry in the config (without hardcoded `AGENT_URLS`).

WHEN an observability datasource is used (Prometheus/Loki/Tempo) THEN its endpoint SHALL come from config (without hardcoding).

WHEN the investigation (spec 18) or the fan-out (spec 17) needs limits (max_agents, evidence ceiling, timeouts) THEN those limits SHALL be configurable.

WHEN the config file is absent or invalid THEN the system SHALL fail at **startup** with an actionable error (which key, what is missing) — not at the first request.

## Acceptance Criteria

- [ ] Single YAML config file (e.g., `config/aigent.yaml`) with sections: `agents[]`, `datasources`, `bedrock` (models by role), `limits`, `cache`, `storage`.
- [ ] Precedence **env > file > default** implemented and tested.
- [ ] Secrets **outside** the YAML — env var or file path (`*_FILE`) only; validated (rejects inline secret).
- [ ] Agent registry replaces `AGENT_URLS` (supervisor reads from config).
- [ ] Datasources (Prometheus/Loki/Tempo/Alertmanager) endpoints in config; hardcoded `PROMETHEUS_URL` removed.
- [ ] `mcp_servers`, GitLab groups, Athena, docs portal → migrated to config (no hardcoding).
- [ ] `bedrock`: model by role (`classifier` → Haiku, `agent`/`synthesis` → Sonnet) configurable (aligns specs 11/17).
- [ ] `limits`: `max_agents`, timeouts, investigation evidence ceiling — configurable.
- [ ] Schema validation at startup (Pydantic) with error message per missing/invalid key.
- [ ] Agent enable/disable via flag in config (disabled agent does not enter fan-out or classifier).
- [ ] `config.example.yaml` + `.env.example` consistent and documented.
- [ ] Tests (test-author ≠ author, ≥90%): precedence env>file>default, inline secret rejection, startup failure with invalid config, agent registry, enable/disable.

## Out of scope

- Hot-reload of config at runtime (future; for now, restart applies config).
- Remote/centralized config (Consul/AppConfig) — starts with local file + env.
- Migration to ExternalSecrets/IRSA (specs 12/13/14 handle prod) — here only the **contract** of "secret via env/file".
