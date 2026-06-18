# How to Create a New Agent

Zero code required. An agent is a directory with 2 files.

## Quick start (30 seconds)

```bash
mkdir agents/my-agent

# 1. Config
cat > agents/my-agent/agent.yaml << 'EOF'
name: my-agent
description: "Describe what this agent does — the classifier uses this for routing."
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
- Always be helpful and concise
- Read-only: never suggest or execute mutations
- Point to Terraform / ArgoCD for any changes
EOF

# 3. Restart
docker compose restart supervisor
```

The agent is live and the classifier will route relevant queries to it automatically.

---

## agent.yaml reference

| Field | Required | Description |
|-------|:--------:|-------------|
| `name` | ✅ | Unique identifier (used as `agent_id` in metrics) |
| `description` | ✅ | What the agent does — used by the LLM classifier for routing |
| `domain` | ✅ | Grouping label (e.g., `cloud`, `security`, `observability`) |
| `capabilities` | ✅ | List of capability strings |
| `datasources` | ✅ | Data sources (can be `[]` for prompt-only agents) |
| `routing_keywords` | ❌ | Fast-path keywords — skip LLM classification when matched |
| `skills` | ❌ | Global skill names this agent may use (lazy-loaded) |
| `cache.ttl` | ❌ | Cache duration in seconds (default: 300) |
| `model.tier` | ❌ | `fast` / `standard` / `premium` (default: `standard`) |
| `model.temperature` | ❌ | LLM temperature (default: 0.1) |
| `read_only` | ❌ | Safety flag (default: `true`) |
| `required_env` | ❌ | Env vars validated at startup |
| `enabled` | ❌ | Set `false` to disable without deleting (default: `true`) |

## Datasource types

| Type | What it does | Key config fields |
|------|-------------|-------------------|
| `boto3` | AWS SDK read-only calls | `services: [ec2, s3, rds, iam, ce, guardduty]` |
| `kubernetes` | K8s cluster state (get/list) | (no extra fields) |
| `http` | Any HTTP GET API | `name`, `url` (supports `${ENV_VAR}`), `headers` |
| `athena` | AWS Athena SELECT queries | `database`, `table`, `workgroup` |
| `mcp` | External MCP server (read-only tool allowlist) | `name`, `url`, `tools: [list_pods, ...]` |

## Skills (lazy-loaded knowledge)

A skill is reusable markdown knowledge injected into the prompt only when the query matches its keywords:

```
skills/
└── oomkill-investigation/
    └── SKILL.md
```

```markdown
---
name: oomkill-investigation
description: How to investigate OOMKilled pods
keywords: [oomkill, oom, "out of memory", "exit code 137"]
---

# Investigating OOMKilled Pods
... knowledge content ...
```

Reference it from `agent.yaml`:

```yaml
skills:
  - oomkill-investigation
```

When a user query matches a keyword, the skill content is appended to the system prompt for that request only. Skills not matched are never loaded — token economy.

## Deploying to Kubernetes

### Option A — Inline in values (configmap)

```yaml
# values.yaml
agentsSource:
  type: configmap

agents:
  - name: my-agent
    config: |
      name: my-agent
      description: "..."
      datasources: []
    prompt: |
      # My Agent
      You are a specialist in X.
```

### Option B — Git repository

```yaml
agentsSource:
  type: git
  repo: https://github.com/your-org/agent-definitions.git
  path: agents/
  ref: main
  tokenSecret: git-token   # Secret with key "token" (GitHub PAT)
```
