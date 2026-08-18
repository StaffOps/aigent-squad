# Design: Config-Driven Agent Platform

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  AGENTS_DIR (volume — source: local/configmap/git/s3)           │
│                                                                 │
│  agents/aws/           agents/finops/        agents/custom/     │
│  ├── agent.yaml        ├── agent.yaml        ├── agent.yaml    │
│  └── prompt.md         ├── prompt.md         └── prompt.md     │
│                        └── examples/                            │
└────────────────────────────────┬────────────────────────────────┘
                                 │ startup discovery
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                      AgentRegistry                               │
│  parse YAML → validate schema → resolve adapters → register     │
└────────────────────────────────┬────────────────────────────────┘
                                 │
         ┌───────────────────────┼───────────────────────┐
         ▼                       ▼                       ▼
   GenericAgent(aws)      GenericAgent(finops)    GenericAgent(custom)
   adapters: [Boto3]      adapters: [Boto3,Athena] adapters: [Http]
   prompt: loaded          prompt: loaded           prompt: loaded
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 │
                    ┌────────────┴────────────┐
                    ▼                         ▼
              Classifier                 Supervisor
              (uses registry)            (routes via registry)
```

## Schema: `agent.yaml`

```yaml
# Required fields
name: aws                           # unique identifier
description: >                      # used by the classifier for routing
  AWS infrastructure specialist.
  Queries EC2, S3, RDS, IAM. Read-only.
domain: cloud-infrastructure        # logical grouping

# Routing fields
capabilities:                       # WHAT it can do
  - ec2_inventory
  - security_group_audit
  - cost_summary
routing_keywords:                   # fast-path without LLM (literal match)
  - ec2
  - instance
  - security group
  - s3 bucket
  - iam role

# Data it collects
datasources:
  - type: boto3
    services: [ec2, s3, rds, iam]
  - type: http
    name: cloudwatch-metrics
    url: "${CLOUDWATCH_ENDPOINT}"
    headers:
      Authorization: "Bearer ${CW_TOKEN}"

# Behavior
cache:
  ttl: 300                          # seconds
  namespace: aws                    # cache isolation
model:
  tier: standard                    # fast (Haiku) | standard (Sonnet) | premium (Opus)
  temperature: 0.1
read_only: true                     # security invariant

# Collaboration (spec 17 — fan-out)
evidence_types: [aws_api_response, cloudwatch_metric]
delegates_to:
  - agent: kubernetes
    when: "issue points to pod/node level (EKS)"
  - agent: finops
    when: "question involves cost attribution"

# Operational
required_env: [AWS_REGION]          # validated at startup
enabled: true                       # false = ignored
port: 8001                          # container port
```

### Optional vs required fields

| Field | Required | Default |
|-------|:--------:|---------|
| `name` | ✅ | — |
| `description` | ✅ | — |
| `domain` | ✅ | — |
| `capabilities` | ✅ | — |
| `datasources` | ✅ | — |
| `routing_keywords` | ❌ | `[]` |
| `cache.ttl` | ❌ | `300` |
| `cache.namespace` | ❌ | `name` |
| `model.tier` | ❌ | `standard` |
| `model.temperature` | ❌ | `0.1` |
| `read_only` | ❌ | `true` |
| `evidence_types` | ❌ | `[]` |
| `delegates_to` | ❌ | `[]` |
| `required_env` | ❌ | `[]` |
| `enabled` | ❌ | `true` |
| `port` | ❌ | auto-assign |

## Components

| Component | Responsibility |
|-----------|----------------|
| `AgentRegistry` | Discovery + validation + access to the roster |
| `GenericAgent` | Single class: load prompt + call adapters + build context + call Bedrock |
| `DatasourceAdapter` (interface) | Contract: `async collect(query, params) → str` |
| `Boto3Adapter` | AWS SDK (read-only by config) |
| `KubernetesAdapter` | kubernetes-client |
| `HttpAdapter` | Any HTTP (Prometheus, GitLab, docs portal, internal APIs) |
| `AthenaAdapter` | AWS Athena queries |
| `Classifier` (updated) | Consumes registry instead of hardcoded list |
| `Supervisor` (updated) | Routes to URL from registry |

## GenericAgent — unified flow

```python
class GenericAgent:
    """Single implementation that works for any agent config."""

    def __init__(self, config: AgentConfig, prompt: str, adapters: list[DatasourceAdapter]):
        self.config = config
        self.prompt = prompt
        self.adapters = adapters

    async def process_request(self, input_text, user_id, session_id, chat_history):
        # 1. Validate input
        # 2. Check cache
        # 3. Collect context from all adapters (parallel)
        contexts = await asyncio.gather(*[a.collect(input_text) for a in self.adapters])
        # 4. Format history
        # 5. Build prompt: system_prompt + contexts + history + query
        # 6. Call Bedrock (model from config.model.tier)
        # 7. Cache response
        # 8. Return ConversationMessage
```

## Datasource Adapters

```python
class DatasourceAdapter(ABC):
    @abstractmethod
    async def collect(self, query: str, params: dict | None = None) -> str:
        """Collect context relevant to the query. Returns formatted string."""
        ...

class Boto3Adapter(DatasourceAdapter):
    def __init__(self, services: list[str], read_only: bool = True): ...

class KubernetesAdapter(DatasourceAdapter):
    def __init__(self, resources: list[str] | None = None): ...

class HttpAdapter(DatasourceAdapter):
    def __init__(self, name: str, url: str, headers: dict | None = None): ...

class AthenaAdapter(DatasourceAdapter):
    def __init__(self, database: str, table: str, workgroup: str): ...
```

Adapters are **stateless** and **read-only** (the `Boto3Adapter` only calls `describe_*`, `list_*`, `get_*`).

## Helm chart (deploy N agents from 1 image)

```yaml
# values.yaml
image:
  repository: harbor.company.com/aigent-squad
  tag: "0.3.0"

agentsSource:
  type: configmap           # configmap | git | s3
  # For git:
  # repo: https://github.com/company/agent-definitions.git
  # path: agents/
  # ref: main

agents:
  - name: aws
    port: 8001
    resources:
      requests: { cpu: 100m, memory: 128Mi }
  - name: kubernetes
    port: 8002
  - name: finops
    port: 8003
  - name: devops
    port: 8004
  - name: observability
    port: 8005
  # Adding a new one: just a line + a dir with agent.yaml+prompt.md
  - name: security
    port: 8010
```

```yaml
# templates/agent-deployment.yaml
{{- range .Values.agents }}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .name }}-agent
  labels:
    app.kubernetes.io/name: {{ .name }}-agent
    app.kubernetes.io/component: agent
spec:
  replicas: 1
  selector:
    matchLabels:
      app: {{ .name }}-agent
  template:
    spec:
      containers:
        - name: agent
          image: {{ $.Values.image.repository }}:{{ $.Values.image.tag }}
          args: ["--agent={{ .name }}"]
          ports:
            - containerPort: {{ .port }}
          env:
            - name: AGENTS_DIR
              value: /config/agents
            - name: AGENT_NAME
              value: {{ .name }}
          volumeMounts:
            - name: agents-config
              mountPath: /config/agents
              readOnly: true
          resources: {{ toYaml (.resources | default $.Values.defaultResources) | nindent 12 }}
      volumes:
        - name: agents-config
          configMap:
            name: agents-config
{{- end }}
```

## Rationale

### Decision 1: Single generic image (not 1 image per agent)

**Choice**: all agents run the same Docker image, differentiated only by config.

**Justification**:
1. **Customizable product**: the end user creates agents without code, Docker, or CI — just YAML + prompt.
2. **Maintenance**: 1 image to patch, update, scan. Not 5+ pipelines.
3. **Extensibility**: from 5 to 50 agents without extra builds.

**Accepted trade-offs**:
| Cost | Reality |
|------|---------|
| Larger image (has all adapters) | ~200MB total — acceptable. Adapters are lightweight Python libs |
| Unused adapter consumes memory? | No — only the ones declared in agent.yaml are instantiated |

**When it would be wrong**: if an agent needs a different runtime (Go, .NET) — then it would be a sidecar. Out of scope.

### Decision 2: Directory-per-agent (filesystem as config)

**Choice**: each agent is a directory (`agent.yaml` + `prompt.md` + extras), not an entry in a monolithic YAML.

**Justification**:
1. **Long prompts don't pollute**: a 200-line `prompt.md` stays in its own file.
2. **Established pattern**: ArgoCD ApplicationSets, Terraform modules, Backstage catalog — all use directory-per-entity.
3. **Git-friendly**: a PR shows the diff of 1 agent without noise from the others.
4. **Extras per agent**: examples/, few-shot.md, RAG docs — live in the same dir.

**Trade-off accepted**: more files vs fewer — FS complexity is manageable with good tooling.

### Decision 3: Directory source is a deploy configuration (not a code concern)

**Choice**: the runtime reads from `AGENTS_DIR` (a path). Where that path comes from (local mount, configmap, git clone, S3) is a **deploy** decision, via Helm values.

**Justification**: decouples the platform from agent configuration. Allows diverse scenarios (local dev with volume, prod with git-sync, multi-tenant with separate buckets) without changing code.

## Invariants

- Zero code to create a new agent (just YAML + prompt).
- 1 Docker image for the entire system (except infra: redis, dynamodb).
- `read_only` honored in all adapters.
- Invalid config → startup failure (fail-fast, not fail-at-runtime).
- Adapters are stateless and parallelizable.

## Migration of the 5 current agents

| Current agent | Migrates to |
|--------------|-------------|
| `src/agents/aws/agent.py` | `agents/aws/agent.yaml` + `prompt.md` (logic absorbed by GenericAgent + Boto3Adapter) |
| `src/agents/kubernetes/agent.py` | `agents/kubernetes/agent.yaml` + `prompt.md` + KubernetesAdapter |
| `src/agents/finops/agent.py` | `agents/finops/agent.yaml` + `prompt.md` + Boto3Adapter + AthenaAdapter |
| `src/agents/devops/agent.py` | `agents/devops/agent.yaml` + `prompt.md` + HttpAdapter(gitlab) + HttpAdapter(docs) |
| `src/agents/observability/agent.py` | `agents/observability/agent.yaml` + `prompt.md` + HttpAdapter(prometheus) |

After migration, `src/agents/*/agent.py` are **deleted** — the logic lives in `GenericAgent` + adapters.

## External dependencies

| Lib | Usage |
|-----|-------|
| `pydantic` | Schema validation of agent.yaml |
| `PyYAML` | Parsing |
| `boto3` | Boto3Adapter |
| `kubernetes` | KubernetesAdapter |
| `httpx` | HttpAdapter |
| Existing in requirements.txt | No new dependencies |
