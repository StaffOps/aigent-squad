# How to Create a New Agent

Zero code required. An agent is a **directory** with 2 files.

## Quick start (30 seconds)

```bash
mkdir agents/my-agent

# 1. Config
cat > agents/my-agent/agent.yaml << 'EOF'
name: my-agent
description: "Describe what this agent does (used by classifier for routing)."
domain: my-domain
capabilities: [what_it_can_do]
routing_keywords: [words, that, trigger, this, agent]
datasources:
  - type: http
    name: my-api
    url: "${MY_API_URL}/endpoint"
cache:
  ttl: 300
read_only: true
EOF

# 2. System prompt
cat > agents/my-agent/prompt.md << 'EOF'
# My Agent

You are a specialist in X. You help users with Y.

## Rules
- Always be helpful
- Read-only: never modify anything
EOF

# 3. Restart (dev) or redeploy (prod)
docker compose restart supervisor
```

The agent is now live and the classifier will route relevant queries to it.

## File reference

### agent.yaml (required)

| Field | Required | Description |
|-------|:--------:|-------------|
| `name` | ✅ | Unique identifier |
| `description` | ✅ | What the agent does (classifier uses this for routing) |
| `domain` | ✅ | Grouping (e.g., cloud, security, observability) |
| `capabilities` | ✅ | List of things it can do |
| `datasources` | ✅ | Data sources it queries (can be `[]` for prompt-only) |
| `routing_keywords` | ❌ | Fast-path keywords (skip LLM classification) |
| `cache.ttl` | ❌ | Cache duration in seconds (default: 300) |
| `model.tier` | ❌ | fast/standard/premium (default: standard) |
| `model.temperature` | ❌ | LLM temperature (default: 0.1) |
| `read_only` | ❌ | Safety flag (default: true) |
| `evidence_types` | ❌ | For RCA workflows |
| `delegates_to` | ❌ | Which agents to ask for help |
| `required_env` | ❌ | Env vars validated at startup |
| `enabled` | ❌ | Set to false to disable (default: true) |

### prompt.md (required)

The system prompt sent to the LLM. Can be as long as needed. Tips:
- Start with who the agent IS
- Define clear rules/constraints
- Specify response format if needed
- Mention read-only policy

## Datasource types

| Type | What it does | Config fields |
|------|-------------|---------------|
| `boto3` | AWS SDK (read-only) | `services: [ec2, s3, rds, iam, ce, guardduty, securityhub]` |
| `kubernetes` | K8s cluster state | (no extra fields needed) |
| `http` | Any HTTP API | `name`, `url` (supports `${ENV_VAR}`), `headers` |
| `athena` | AWS Athena queries | `database`, `table`, `workgroup` |

## Examples

### Prompt-only agent (no datasources)

```yaml
name: advisor
description: "General cloud architecture advisor."
domain: architecture
capabilities: [architecture_review, best_practices]
routing_keywords: [architecture, design, best practice, pattern]
datasources: []
read_only: true
```

### Multi-datasource agent

```yaml
name: cost-security
description: "Correlates cost anomalies with security findings."
domain: finops-security
capabilities: [cost_anomaly_security_correlation]
datasources:
  - type: boto3
    services: [ce, guardduty]
  - type: http
    name: slack-alerts
    url: "${SLACK_WEBHOOK_URL}"
required_env: [AWS_REGION, SLACK_WEBHOOK_URL]
```

## Deploy

### Local (docker compose)
Agents are mounted from `./agents/` directory. Just restart:
```bash
docker compose restart supervisor
```

### Kubernetes (Helm)
Two options in `values.yaml`:

**Option A — Inline in values (configmap source):**
```yaml
agentsSource:
  type: configmap

agents:
  - name: my-agent
    config: |
      name: my-agent
      description: "..."
      ...
    prompt: |
      # My Agent
      ...
```

**Option B — Git repository (git source):**
```yaml
agentsSource:
  type: git
  repo: https://github.com/your-org/agent-definitions.git
  path: agents/
  ref: main
```
