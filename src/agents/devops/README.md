# DevOps Agent

Especialista em CI/CD e documentação (read-only) com suporte a conversação contextual.

## Função

- Analisa pipelines e workflows
- Consulta documentação interna
- Sugere correções via Git/PR
- **Mantém contexto conversacional** por usuário/sessão
- **100% read-only** - nunca trigger pipelines ou modifica infra

## Dependências

### AWS
- **Bedrock**: Claude 3.5 Sonnet (Converse API)
- **DynamoDB**: Conversation history (gerenciado pelo Supervisor)

### Infraestrutura
- **Redis**: Cache (TTL 5min)
- **Docs Portal**: API de documentação (opcional)

### Opcional
- **GitHub API**: Status de workflows
- **GitLab API**: Status de pipelines

## Environment Variables

```bash
# AWS
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=anthropic.claude-sonnet-4-5-20250929-v1:0

# Redis
REDIS_HOST=<endpoint>
REDIS_PORT=6379
REDIS_SSL=true

# Opcional: Documentação
DOCS_PORTAL_URL=https://docs.company.com
DOCS_PORTAL_TOKEN=<token>

# Opcional: GitHub
GITHUB_TOKEN=ghp_...
GITHUB_ORG=your-org

# Opcional: GitLab
GITLAB_TOKEN=glpat-...
GITLAB_URL=https://gitlab.com
```

## IAM Permissions

```json
{
  "Effect": "Allow",
  "Action": ["bedrock:InvokeModel"],
  "Resource": "arn:aws:bedrock:*::foundation-model/*"
},
{
  "Effect": "Deny",
  "Action": ["*:Create*", "*:Update*", "*:Delete*", "*:Trigger*"],
  "Resource": "*"
}
```

## API Endpoints

### `POST /process`
Processa query DevOps com conversation history.

**Request**:
```json
{
  "input_text": "Como fazer rollback?",
  "user_id": "user123",
  "session_id": "session456",
  "chat_history": [
    {
      "role": "user",
      "content": "Como fazer deploy?",
      "timestamp": "2026-02-14T10:00:00Z"
    },
    {
      "role": "assistant",
      "content": "Para fazer deploy, siga estes passos...",
      "timestamp": "2026-02-14T10:00:05Z"
    }
  ]
}
```

**Response**:
```json
{
  "role": "assistant",
  "content": "Para fazer rollback do deploy que mencionei:\n1. Acesse ArgoCD...",
  "timestamp": "2026-02-14T10:01:00Z",
  "agent_id": "devops"
}
```

### `GET /health`
Health check.

## Conversation History

O agent recebe **apenas seu próprio histórico** (isolado de outros agents):
- Mantém contexto de conversas anteriores sobre DevOps
- Não vê conversas com AWS, Kubernetes, etc
- Histórico gerenciado automaticamente pelo Supervisor

## Integração com Docs Portal

Se configurado, busca documentação antes de responder:

```python
# Busca runbooks, guides, standards
docs = search_portal("deploy workflow")
# Cita fontes nas respostas
response = f"Segundo a documentação [link], o processo é..."
```

### Docs Portal API
```bash
# Exemplo de busca
curl -H "Authorization: Bearer $DOCS_PORTAL_TOKEN" \
  "https://docs.company.com/api/search?q=rollback"
```

## Customização

Edite `prompt.md` para adicionar:
- Stack tecnológico (GitHub Actions, GitLab CI, ArgoCD, etc)
- Políticas de CI/CD
- Branch strategy (GitFlow, trunk-based, etc)
- Workflows comuns
- Runbooks importantes

## Deployment

### Docker
```bash
docker build -f Dockerfile -t devops-agent .
docker run -p 8004:8004 --env-file .env devops-agent
```

### Kubernetes
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: devops-agent
spec:
  replicas: 1
  template:
    spec:
      containers:
      - name: devops-agent
        image: <ECR>/agent-squad-devops:latest
        ports:
        - containerPort: 8004
        env:
        - name: DOCS_PORTAL_URL
          value: "https://docs.company.com"
        - name: DOCS_PORTAL_TOKEN
          valueFrom:
            secretKeyRef:
              name: agent-squad-secrets
              key: docs-token
```

## Escala

- **Min replicas**: 1
- **Max replicas**: 5
- **Cache TTL**: 5min
- **HPA**: CPU > 70%

## Exemplo de Uso

### Query Simples
```bash
curl -X POST http://localhost:8004/process \
  -H "Content-Type: application/json" \
  -d '{
    "input_text": "Como fazer deploy no EKS?",
    "user_id": "user123",
    "session_id": "session456",
    "chat_history": []
  }'
```

### Query com Follow-up
```bash
# Primeira pergunta
curl -X POST http://localhost:8004/process \
  -d '{"input_text": "Explique o processo de deploy", ...}'

# Follow-up (agent lembra do contexto)
curl -X POST http://localhost:8004/process \
  -d '{"input_text": "E se der erro?", ...}'
```

## MCP Servers

### Opcional: Docs Portal MCP
Se você tem um MCP server para documentação interna:

```yaml
# mcp-server-docs.yaml
name: docs-portal
endpoint: http://docs-mcp:8080
tools:
  - search_docs
  - get_runbook
  - list_guides
```

O agent descobrirá e usará automaticamente.
