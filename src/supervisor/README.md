# Supervisor Agent

Orquestra e delega tarefas para especialistas usando **Classifier inteligente** e conversation history.

## Função

- Recebe requests do Slack (ou API direta)
- **Classifica intent** com IA (Bedrock Claude)
- Detecta follow-ups e context switching
- Delega para especialista apropriado via HTTP
- Mantém contexto conversacional isolado por agent
- Consolida respostas

## Arquitetura v2.0

```
User → Supervisor → Classifier (IA) → Seleciona Agent
                  ↓
            DynamoDB (conversation history)
                  ↓
            HTTP call → Specialist Agent
                  ↓
            Salva resposta → DynamoDB
                  ↓
            Retorna ao usuário
```

### Mudanças vs v1.0
- ❌ **Removido**: LangGraph manual routing
- ✅ **Adicionado**: Classifier inteligente com IA
- ✅ **Adicionado**: Follow-up detection automático
- ✅ **Adicionado**: Context switching inteligente
- ✅ **Adicionado**: Conversation history separado (global vs isolado)

## Dependências

### AWS
- **Bedrock**: Claude 3.5 Sonnet (Classifier + Agents)
- **DynamoDB**: Table `agent-sessions` (conversation history)

### Infraestrutura
- **Redis**: Cache (opcional)
- **Agents**: HTTP endpoints dos 5 especialistas
  - AWS Agent: `http://aws-agent:8001/process`
  - Kubernetes Agent: `http://kubernetes-agent:8002/process`
  - FinOps Agent: `http://finops-agent:8003/process`
  - DevOps Agent: `http://devops-agent:8004/process`
  - Observability Agent: `http://observability-agent:8005/process`

### Kubernetes
- **ServiceAccount**: `agent-squad-supervisor` com IRSA
- **Secrets**: Slack tokens, DynamoDB access

## Environment Variables

```bash
# AWS
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-5-20250929-v1:0

# DynamoDB
DYNAMODB_SESSIONS_TABLE=agent-sessions
DYNAMODB_ENDPOINT=  # Opcional: para local development

# Redis (opcional)
REDIS_HOST=<endpoint>
REDIS_PORT=6379
REDIS_SSL=true

# Slack (opcional)
SLACK_BOT_TOKEN=xoxb-...
SLACK_SIGNING_SECRET=...
SLACK_PROACTIVE_CHANNEL=C12345678

# Agent Endpoints (K8s service discovery)
AWS_AGENT_URL=http://aws-agent:8001/process
KUBERNETES_AGENT_URL=http://kubernetes-agent:8002/process
FINOPS_AGENT_URL=http://finops-agent:8003/process
DEVOPS_AGENT_URL=http://devops-agent:8004/process
OBSERVABILITY_AGENT_URL=http://observability-agent:8005/process
```

## IAM Permissions

```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock:InvokeModel"
  ],
  "Resource": "arn:aws:bedrock:*::foundation-model/*"
},
{
  "Effect": "Allow",
  "Action": [
    "dynamodb:GetItem",
    "dynamodb:PutItem",
    "dynamodb:Query",
    "dynamodb:DeleteItem"
  ],
  "Resource": "arn:aws:dynamodb:*:*:table/agent-sessions"
}
```

## DynamoDB Schema

### Table: `agent-sessions`
```
Partition Key: pk (String) = "user_id#session_id"
Sort Key: sk (String) = "agent_id#timestamp"
TTL: ttl (Number) = 24 hours

Attributes:
- user_id (String)
- session_id (String)
- agent_id (String)
- role (String) = "user" | "assistant"
- content (String)
- timestamp (String) = ISO 8601
```

### Example Item
```json
{
  "pk": "user123#session456",
  "sk": "aws#2026-02-14T10:00:00Z",
  "user_id": "user123",
  "session_id": "session456",
  "agent_id": "aws",
  "role": "user",
  "content": "Quantas instâncias EC2?",
  "timestamp": "2026-02-14T10:00:00Z",
  "ttl": 1739548800
}
```

## API Endpoints

### `POST /query`
Processa query do usuário com roteamento inteligente.

**Request**:
```json
{
  "user_input": "Quantas instâncias EC2 estão rodando?",
  "user_id": "user123",
  "session_id": "session456"
}
```

**Response**:
```json
{
  "agent": "aws",
  "response": "Você tem 12 instâncias EC2 rodando...",
  "confidence": 0.95,
  "reasoning": "User is asking about AWS EC2 instances"
}
```

### `GET /health`
Health check.

**Response**:
```json
{
  "status": "healthy",
  "service": "supervisor"
}
```

## Classifier

### Como Funciona

1. **Busca histórico global** (todas conversas do user/session)
2. **Analisa com IA**:
   - User input
   - Agent descriptions
   - Conversation history
3. **Detecta**:
   - Follow-ups ("sim", "ok", "1") → mantém mesmo agent
   - Context switching ("agora sobre custos") → troca agent
4. **Retorna**:
   - `selected_agent`: "aws" | "kubernetes" | "finops" | "devops" | "observability"
   - `confidence`: 0.0 - 1.0
   - `reasoning`: Explicação da decisão

### Agent Descriptions (usado pelo Classifier)
```python
AGENT_DESCRIPTIONS = {
    "aws": "AWS resources (EC2, S3, RDS, Lambda, VPC, IAM)",
    "kubernetes": "K8s clusters (pods, nodes, deployments, services)",
    "finops": "Cloud costs and financial optimization",
    "devops": "CI/CD pipelines, GitLab, automation, documentation",
    "observability": "Metrics, logs, alerts, anomaly detection"
}
```

## Conversation History

### Dois Níveis

1. **Global** (Classifier vê):
   - Todas conversas do user/session
   - Todos agents
   - Usado para classificação

2. **Isolado** (Agent vê):
   - Apenas conversas com aquele agent específico
   - Não vê conversas com outros agents
   - Usado para processar request

### Exemplo
```
User: "Quantas instâncias EC2?" → AWS Agent
User: "E pods?" → Kubernetes Agent
User: "Volte para EC2" → AWS Agent (classifier detecta context switch)
User: "Quantas em us-east-1?" → AWS Agent (follow-up, mantém agent)
```

## Deployment

### Docker (Local)
```bash
docker build -t supervisor .
docker run -p 8000:8000 --env-file .env supervisor
```

### Kubernetes
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: supervisor
  namespace: agent-squad
spec:
  replicas: 2
  selector:
    matchLabels:
      app: supervisor
  template:
    metadata:
      labels:
        app: supervisor
    spec:
      serviceAccountName: agent-squad-supervisor
      containers:
      - name: supervisor
        image: <ECR>/agent-squad-supervisor:latest
        ports:
        - containerPort: 8000
        env:
        - name: AWS_REGION
          value: "us-east-1"
        - name: DYNAMODB_SESSIONS_TABLE
          value: "agent-sessions"
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 30
        readinessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 10
```

## Escala

- **Min replicas**: 2
- **Max replicas**: 10
- **HPA**: CPU > 70% ou Memory > 80%
- **Request timeout**: 30s (HTTP calls para agents)

## Exemplo de Uso

### Query Simples
```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "user_input": "Quantas instâncias EC2?",
    "user_id": "user123",
    "session_id": "session456"
  }'
```

### Conversação Multi-turn
```bash
# 1. Primeira pergunta (AWS)
curl -X POST http://localhost:8000/query \
  -d '{"user_input": "Mostre instâncias EC2", "user_id": "user123", "session_id": "session456"}'
# → Classifier seleciona: aws

# 2. Follow-up (mantém AWS)
curl -X POST http://localhost:8000/query \
  -d '{"user_input": "Quantas em us-east-1?", "user_id": "user123", "session_id": "session456"}'
# → Classifier detecta follow-up, mantém: aws

# 3. Context switch (troca para FinOps)
curl -X POST http://localhost:8000/query \
  -d '{"user_input": "Quanto custam?", "user_id": "user123", "session_id": "session456"}'
# → Classifier detecta mudança de tópico, seleciona: finops

# 4. Follow-up (mantém FinOps)
curl -X POST http://localhost:8000/query \
  -d '{"user_input": "E no mês passado?", "user_id": "user123", "session_id": "session456"}'
# → Classifier detecta follow-up, mantém: finops
```

## Slack Integration (Opcional)

### Configuração
```python
# src/supervisor/slack.py
from slack_bolt import App

app = App(
    token=os.environ["SLACK_BOT_TOKEN"],
    signing_secret=os.environ["SLACK_SIGNING_SECRET"]
)

@app.message()
async def handle_message(message, say):
    response = await supervisor.process_request(
        user_input=message["text"],
        user_id=message["user"],
        session_id=message["channel"]
    )
    await say(response["response"])
```

## MCP Servers

Não requer MCP servers. Usa:
- Bedrock API direta (boto3)
- DynamoDB API (boto3)
- HTTP calls para specialist agents
