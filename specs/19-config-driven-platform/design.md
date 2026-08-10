# Design: Config-Driven Platform

## Architecture

A single config loading point at startup, with precedence **env > file > default**, validated by Pydantic. Replaces the scattered hardcoded constants.

```
config/aigent.yaml  ──┐
env vars            ──┼─▶  load_config()  ─▶  AppConfig (validated)  ─▶  injected into components
defaults (in schema)──┘        (startup; fails early if invalid)
secrets (env / *_FILE) ─────────────────────────────────────────────▶  resolved outside of YAML
```

## Config file structure (YAML)

```yaml
# config/aigent.yaml  (no secrets — only structure/endpoints)
bedrock:
  region: us-east-1
  models:
    classifier: anthropic.claude-3-5-haiku-20241022-v1:0   # cheap (spec 11)
    agent:      anthropic.claude-sonnet-4-5-20250929-v1:0
    synthesis:  anthropic.claude-sonnet-4-5-20250929-v1:0

agents:                       # registry — replaces hardcoded AGENT_URLS
  - name: aws
    url: http://aws-agent:8001/process
    enabled: true
  - name: kubernetes
    url: http://kubernetes-agent:8002/process
    enabled: true
  # finops / devops / observability ...

datasources:                  # replaces hardcoded PROMETHEUS_URL etc.
  prometheus: http://prometheus.monitoring.svc.cluster.local:9090
  loki:       http://loki-gateway.monitoring:80
  tempo:      http://tempo-gateway.monitoring:80

limits:
  max_agents: 3               # fan-out ceiling (spec 17)
  agent_timeout_s: 25
  investigation_evidence_cap: 8   # evidence query ceiling (spec 18)

storage:
  dynamodb_table: aigent-sessions
  session_ttl_hours: 168
cache:
  redis_host: redis
  redis_port: 6379
  redis_ssl: true
```

Secrets **do not** appear here — `redis_password`, `gitlab_token`, etc. come from env (`REDIS_PASSWORD`) or file (`GITLAB_TOKEN_FILE=/etc/secrets/gitlab`).

## Components

| Component | Responsibility | Location |
|-----------|----------------|----------|
| `AppConfig` (Pydantic) | Schema + validation + defaults | `src/core/config.py` (refactored) |
| `load_config()` | Reads YAML, applies env overrides, validates, resolves secrets | `src/core/config.py` |
| `AgentRegistry` | List of enabled agents + lookup by name | derived from `AppConfig.agents` |

## Precedence (env > file > default)

```python
# Pydantic Settings with custom source:
# 1. defaults in the model
# 2. YAML file (config_path env or ./config/aigent.yaml)
# 3. env vars (AIGENT__BEDROCK__MODELS__CLASSIFIER=... using delimiter __)
# Priority order: env  >  yaml  >  default
```

`pydantic-settings` v2 supports `settings_customise_sources` + `YamlConfigSettingsSource` + nested delimiter — covers all three levels without manual merge code.

## Secrets (contract)

- In the YAML: **forbidden** for secret values (validator rejects known secret keys with inline values).
- Allowed: `redis_password` via env `REDIS_PASSWORD`, or `*_FILE` pointing to a mounted file (read at startup).
- Aligns with 12-factor / `cloud-security` (file-mounted preferred); migration to ExternalSecrets is in specs 12–14.

## Rationale (decisions and trade-offs)

### Decision 1: YAML file + env override (not env-only, not remote config)

**Choice**: declarative config in a YAML file, with env vars overriding, validated at startup.

**Justification, in order of strength**:
1. **The product needs readable, versionable config** — an agent/datasource registry in YAML is inspectable and diffable; env-only (the current state with `AGENT_URLS` in code + scattered flags) does not scale to dozens of keys.
2. **Env override is table-stakes in K8s** — per-environment values (DEV/HML/PRD) override the base file without rebuilding the image (12-factor III).
3. **Failing at startup** with invalid config is much better than discovering it at the 1st request in production.

**Accepted trade-offs**:
| Cost | Reality |
|------|---------|
| One more file to maintain | It is the single source — eliminates hardcoding scattered in 4+ places |
| `pydantic-settings` nested sources has a learning curve | Solves merge/precedence without manual code; well documented |

**When it would be wrong** (signals): if the number of instances/environments grows to the point of requiring dynamic centralized config → migrate to AWS AppConfig/Consul (out of scope now).

**Alternatives discarded**:
- **Env-only** — does not scale for an agent/datasource registry; unreadable.
- **Remote config (AppConfig/Consul) right now** — premature complexity; the user asked for "not overly complex."

### Decision 2: Agent registry in config (kills hardcoded `AGENT_URLS`)

**Choice**: the supervisor reads the list of agents (url, enabled) from config, not from a constant.

**Justification**:
1. **Productization**: adding/removing/disabling an agent becomes a config change, not a code change + rebuild.
2. **Enables 17/18**: fan-out and investigation iterate over "enabled agents" — needs to be data, not a constant.

**Trade-off accepted**: one extra lookup at startup — irrelevant compared to the flexibility gained.

### Decision 3: Secrets outside the YAML, always

**Choice**: the versioned YAML never contains secrets; the validator rejects them.

**Justification**: security (`cloud-security`, 12-factor) — a secret in a versioned file is the classic anti-pattern. Fail-closed: if someone puts one inline, startup refuses.

## Invariants

- Precedence **env > file > default** — always.
- No secret in versioned YAML (validated).
- Invalid config = **startup failure** (never at request time).
- Agent `enabled: false` does not enter classifier/fan-out/investigation.
- Zero hardcoded endpoint/credential in the code after this spec.

## External dependencies

| Lib | Usage |
|-----|-------|
| `pydantic-settings` v2 | schema + sources (env/yaml) + validation |
| `PyYAML` | config file parsing |

## Verification

```bash
docker run --rm -v $(pwd):/app -w /app python:3.11-slim sh -c \
  "pip install -q -r requirements.txt pytest && pytest tests/ -v --cov=src --cov-fail-under=90"
```

Key tests (test-author ≠ author): env overrides yaml overrides default; inline secret in yaml → validation error; absent/invalid yaml → startup failure with indicated key; registry returns only `enabled` agents; `*_FILE` reads secret from file.

## Risks

- Migration breaks something that depended on hardcoding — mitigate by doing the refactor with spec 02 tests green.
- Drift between `config.example.yaml` and `.env.example` — maintain consistency (documentation-sync) and cover in `--check` if available.
