# Kubernetes Agent

Especialista em cluster Kubernetes (read-only) com suporte a conversação contextual.

## Função

- Analisa estado do cluster (pods, nodes, deployments)
- Identifica problemas (CrashLoopBackOff, OOM, etc)
- Sugere correções via ArgoCD/GitOps
- **Mantém contexto conversacional** por usuário/sessão
- **100% read-only** - nunca modifica recursos

## Dependências

### Kubernetes
- **API Server**: Acesso ao cluster
- **RBAC**: Permissions get/list/watch

### AWS
- **Bedrock**: Claude 3.5 Sonnet (Converse API)
- **DynamoDB**: Conversation history (gerenciado pelo Supervisor)

### Infraestrutura
- **Redis**: Cache de cluster state (TTL 1min)

## Environment Variables

```bash
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=anthropic.claude-sonnet-4-5-20250929-v1:0
REDIS_HOST=<endpoint>
REDIS_PORT=6379
REDIS_SSL=true
```

## Kubernetes RBAC

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: agent-squad-kubernetes
rules:
- apiGroups: [""]
  resources: ["pods", "nodes", "services", "configmaps", "secrets"]
  verbs: ["get", "list", "watch"]
- apiGroups: ["apps"]
  resources: ["deployments", "statefulsets", "daemonsets", "replicasets"]
  verbs: ["get", "list", "watch"]
- apiGroups: ["batch"]
  resources: ["jobs", "cronjobs"]
  verbs: ["get", "list", "watch"]
```

## IAM Permissions

```json
{
  "Effect": "Allow",
  "Action": ["bedrock:InvokeModel"],
  "Resource": "arn:aws:bedrock:*::foundation-model/*"
}
```

## API Endpoints

### `POST /process`
Processa query Kubernetes com conversation history.

**Request**:
```json
{
  "input_text": "Quais pods estão em CrashLoopBackOff?",
  "user_id": "user123",
  "session_id": "session456",
  "chat_history": [
    {
      "role": "user",
      "content": "Mostre os nodes",
      "timestamp": "2026-02-14T10:00:00Z"
    },
    {
      "role": "assistant",
      "content": "Você tem 5 nodes...",
      "timestamp": "2026-02-14T10:00:05Z"
    }
  ]
}
```

**Response**:
```json
{
  "role": "assistant",
  "content": "Encontrei 3 pods em CrashLoopBackOff...",
  "timestamp": "2026-02-14T10:01:00Z",
  "agent_id": "kubernetes"
}
```

### `GET /health`
Health check.

## Conversation History

O agent recebe **apenas seu próprio histórico** (isolado de outros agents):
- Mantém contexto de conversas anteriores sobre K8s
- Não vê conversas com AWS, FinOps, etc
- Histórico gerenciado automaticamente pelo Supervisor

## Customização

Edite `prompt.md` para adicionar:
- Versão do cluster
- Políticas de resources (requests/limits)
- Namespaces importantes
- Ferramentas (Istio, Linkerd, etc)

## Deployment

### Docker (Local)
```bash
docker build -f Dockerfile -t kubernetes-agent .
docker run -p 8002:8002 --env-file .env \
  -v ~/.kube:/root/.kube:ro kubernetes-agent
```

### Kubernetes
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kubernetes-agent
spec:
  replicas: 2
  template:
    spec:
      serviceAccountName: agent-squad-kubernetes
      containers:
      - name: kubernetes-agent
        image: <ECR>/agent-squad-kubernetes:latest
        ports:
        - containerPort: 8002
```

## Escala

- **Min replicas**: 2
- **Max replicas**: 10
- **Cache TTL**: 1min (cluster state)
- **HPA**: CPU > 70%

## Exemplo de Uso

### Query Simples
```bash
curl -X POST http://localhost:8002/process \
  -H "Content-Type: application/json" \
  -d '{
    "input_text": "Quais pods estão com restart alto?",
    "user_id": "user123",
    "session_id": "session456",
    "chat_history": []
  }'
```

### Query com Follow-up
```bash
# Primeira pergunta
curl -X POST http://localhost:8002/process \
  -d '{"input_text": "Mostre pods do namespace production", ...}'

# Follow-up (agent lembra do contexto)
curl -X POST http://localhost:8002/process \
  -d '{"input_text": "Quais estão usando mais memória?", ...}'
```

## MCP Servers

Não requer MCP servers externos. Usa Kubernetes API nativa via client Python.
