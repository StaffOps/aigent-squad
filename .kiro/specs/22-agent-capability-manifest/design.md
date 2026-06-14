# Design: Config-Driven Agent Platform

## Arquitetura

```
┌─────────────────────────────────────────────────────────────────┐
│  AGENTS_DIR (volume — fonte: local/configmap/git/s3)            │
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
              (usa registry)             (roteia por registry)
```

## Schema: `agent.yaml`

```yaml
# Campos obrigatórios
name: aws                           # identificador único
description: >                      # usado pelo classifier para roteamento
  AWS infrastructure specialist.
  Queries EC2, S3, RDS, IAM. Read-only.
domain: cloud-infrastructure        # agrupamento lógico

# Campos de roteamento
capabilities:                       # O QUE sabe fazer
  - ec2_inventory
  - security_group_audit
  - cost_summary
routing_keywords:                   # fast-path sem LLM (match literal)
  - ec2
  - instance
  - security group
  - s3 bucket
  - iam role

# Dados que coleta
datasources:
  - type: boto3
    services: [ec2, s3, rds, iam]
  - type: http
    name: cloudwatch-metrics
    url: "${CLOUDWATCH_ENDPOINT}"
    headers:
      Authorization: "Bearer ${CW_TOKEN}"

# Comportamento
cache:
  ttl: 300                          # segundos
  namespace: aws                    # isolamento de cache
model:
  tier: standard                    # fast (Haiku) | standard (Sonnet) | premium (Opus)
  temperature: 0.1
read_only: true                     # invariante de segurança

# Colaboração (spec 17 — fan-out)
evidence_types: [aws_api_response, cloudwatch_metric]
delegates_to:
  - agent: kubernetes
    when: "issue points to pod/node level (EKS)"
  - agent: finops
    when: "question involves cost attribution"

# Operacional
required_env: [AWS_REGION]          # validado no startup
enabled: true                       # false = ignorado
port: 8001                          # porta do container
```

### Campos opcionais vs obrigatórios

| Campo | Obrigatório | Default |
|-------|:-----------:|---------|
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

## Componentes

| Componente | Responsabilidade |
|-----------|------------------|
| `AgentRegistry` | Discovery + validação + acesso ao roster |
| `GenericAgent` | Classe única: load prompt + call adapters + build context + call Bedrock |
| `DatasourceAdapter` (interface) | Contrato: `async collect(query, params) → str` |
| `Boto3Adapter` | AWS SDK (read-only by config) |
| `KubernetesAdapter` | kubernetes-client |
| `HttpAdapter` | Qualquer HTTP (Prometheus, GitLab, docs portal, APIs internas) |
| `AthenaAdapter` | AWS Athena queries |
| `Classifier` (atualizado) | Consome registry em vez de lista hardcoded |
| `Supervisor` (atualizado) | Roteia para URL do registry |

## GenericAgent — fluxo unificado

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

Adapters são **stateless** e **read-only** (o `Boto3Adapter` só chama `describe_*`, `list_*`, `get_*`).

## Helm chart (deploy N agentes de 1 imagem)

```yaml
# values.yaml
image:
  repository: harbor.company.com/aigent-squad
  tag: "0.3.0"

agentsSource:
  type: configmap           # configmap | git | s3
  # Para git:
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
  # Adicionar um novo: basta uma linha + um dir com agent.yaml+prompt.md
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

### Decisão 1: Imagem única genérica (não 1 imagem por agente)

**Escolha**: todos os agentes rodam a mesma imagem Docker, diferenciados apenas por config.

**Justificativa**:
1. **Produto customizável**: o usuário final cria agentes sem código, Docker, ou CI — só YAML + prompt.
2. **Manutenção**: 1 imagem para patchar, atualizar, scannear. Não 5+ pipelines.
3. **Extensibilidade**: de 5 para 50 agentes sem build a mais.

**Trade-offs aceitos**:
| Custo | Realidade |
|-------|-----------|
| Imagem maior (tem todos os adapters) | ~200MB total — aceitável. Adapters são libs Python leves |
| Adapter não usado consome memória? | Não — instanciado só o declarado no agent.yaml |

**Quando estaria errada**: se um agente precisar de runtime diferente (Go, .NET) — aí seria sidecar. Fora de escopo.

### Decisão 2: Directory-per-agent (filesystem as config)

**Escolha**: cada agente é um diretório (`agent.yaml` + `prompt.md` + extras), não uma entrada num YAML monolítico.

**Justificativa**:
1. **Prompts longos não poluem**: um `prompt.md` de 200 linhas fica em arquivo próprio.
2. **Padrão estabelecido**: ArgoCD ApplicationSets, Terraform modules, Backstage catalog — todos usam directory-per-entity.
3. **Git-friendly**: PR mostra diff de 1 agent sem ruído dos outros.
4. **Extras por agent**: examples/, few-shot.md, RAG docs — vivem no mesmo dir.

**Trade-off aceito**: mais arquivos vs menos — complexidade de FS é gerenciável com bom tooling.

### Decisão 3: Fonte do diretório é configuração de deploy (não de código)

**Escolha**: o runtime lê de `AGENTS_DIR` (um path). De onde esse path vem (local mount, configmap, git clone, S3) é decisão de **deploy**, via Helm values.

**Justificativa**: desacopla plataforma de configuração de agentes. Permite cenários diversos (dev local com volume, prod com git-sync, multi-tenant com buckets separados) sem mudar código.

## Invariantes

- Zero código para criar um agente novo (só YAML + prompt).
- 1 imagem Docker para todo o sistema (exceto infra: redis, dynamodb).
- `read_only` honrado em todos os adapters.
- Config inválida → falha no startup (fail-fast, não fail-runtime).
- Adapters são stateless e paralelizáveis.

## Migração dos 5 agentes atuais

| Agente atual | Migra para |
|-------------|------------|
| `src/agents/aws/agent.py` | `agents/aws/agent.yaml` + `prompt.md` (lógica absorvida pelo GenericAgent + Boto3Adapter) |
| `src/agents/kubernetes/agent.py` | `agents/kubernetes/agent.yaml` + `prompt.md` + KubernetesAdapter |
| `src/agents/finops/agent.py` | `agents/finops/agent.yaml` + `prompt.md` + Boto3Adapter + AthenaAdapter |
| `src/agents/devops/agent.py` | `agents/devops/agent.yaml` + `prompt.md` + HttpAdapter(gitlab) + HttpAdapter(docs) |
| `src/agents/observability/agent.py` | `agents/observability/agent.yaml` + `prompt.md` + HttpAdapter(prometheus) |

Após migração, `src/agents/*/agent.py` são **deletados** — a lógica vive no `GenericAgent` + adapters.

## Dependências externas

| Lib | Uso |
|-----|-----|
| `pydantic` | Schema validation do agent.yaml |
| `PyYAML` | Parse |
| `boto3` | Boto3Adapter |
| `kubernetes` | KubernetesAdapter |
| `httpx` | HttpAdapter |
| Existentes no requirements.txt | Nenhuma dep nova |
