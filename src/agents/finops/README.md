# FinOps Agent

Especialista em custos AWS e Kubernetes (read-only) com suporte a conversação contextual.

## Função

- Analisa custos AWS (Cost Explorer)
- Analisa custos Kubernetes (Kubecost via Athena)
- Identifica idle resources e oportunidades
- Calcula ROI de otimizações
- **Mantém contexto conversacional** por usuário/sessão
- **100% read-only** - nunca compra RIs ou modifica budgets

## Dependências

### AWS
- **Cost Explorer**: Custos AWS
- **Athena**: Queries Kubecost
- **S3**: Bucket Athena results
- **Bedrock**: Claude 3.5 Sonnet (Converse API)
- **DynamoDB**: Conversation history (gerenciado pelo Supervisor)

### Infraestrutura
- **Redis**: Cache de custos (TTL 1h)
- **Kubecost**: Database Athena configurado

## Environment Variables

```bash
# AWS
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=anthropic.claude-sonnet-4-5-20250929-v1:0

# Redis
REDIS_HOST=<endpoint>
REDIS_PORT=6379
REDIS_SSL=true

# Kubecost Athena
ATHENA_PROJECT_ID=123456789012
ATHENA_BUCKET=s3://company-athena-kubecost
ATHENA_REGION=us-east-1
ATHENA_DATABASE=kubecost
ATHENA_TABLE=kubecost_split
ATHENA_WORKGROUP=primary
```

## IAM Permissions

```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock:InvokeModel",
    "ce:GetCostAndUsage",
    "ce:GetCostForecast",
    "ce:GetDimensionValues",
    "athena:StartQueryExecution",
    "athena:GetQueryExecution",
    "athena:GetQueryResults",
    "glue:GetDatabase",
    "glue:GetTable",
    "s3:GetObject",
    "s3:PutObject",
    "s3:ListBucket"
  ],
  "Resource": [
    "arn:aws:bedrock:*::foundation-model/*",
    "arn:aws:athena:*:*:workgroup/primary",
    "arn:aws:glue:*:*:database/kubecost",
    "arn:aws:glue:*:*:table/kubecost/*",
    "arn:aws:s3:::company-athena-kubecost/*"
  ]
},
{
  "Effect": "Deny",
  "Action": ["*:Purchase*", "*:Create*", "*:Update*", "*:Modify*"],
  "Resource": "*"
}
```

## API Endpoints

### `POST /process`
Processa query FinOps com conversation history.

**Request**:
```json
{
  "input_text": "Quais namespaces custam mais?",
  "user_id": "user123",
  "session_id": "session456",
  "chat_history": [
    {
      "role": "user",
      "content": "Quanto gastamos este mês?",
      "timestamp": "2026-02-14T10:00:00Z"
    },
    {
      "role": "assistant",
      "content": "Gastamos $5,432 este mês...",
      "timestamp": "2026-02-14T10:00:05Z"
    }
  ]
}
```

**Response**:
```json
{
  "role": "assistant",
  "content": "Top 3 namespaces por custo:\n1. production: $2,100\n2. staging: $890\n3. dev: $450",
  "timestamp": "2026-02-14T10:01:00Z",
  "agent_id": "finops"
}
```

### `GET /health`
Health check.

## Conversation History

O agent recebe **apenas seu próprio histórico** (isolado de outros agents):
- Mantém contexto de conversas anteriores sobre custos
- Não vê conversas com AWS, Kubernetes, etc
- Histórico gerenciado automaticamente pelo Supervisor

## Kubecost Integration

### Athena Table Schema
```sql
CREATE EXTERNAL TABLE kubecost_split (
  namespace string,
  pod string,
  cost double,
  cpu_allocation double,
  memory_allocation double,
  date date
)
STORED AS PARQUET
LOCATION 's3://company-athena-kubecost/data/'
```

### Query Examples
```sql
-- Top namespaces por custo (últimos 30 dias)
SELECT namespace, SUM(cost) as total_cost
FROM kubecost.kubecost_split
WHERE date >= current_date - interval '30' day
GROUP BY namespace
ORDER BY total_cost DESC
LIMIT 10
```

## Customização

Edite `prompt.md` para adicionar:
- Budgets mensais por projeto
- Políticas de otimização
- Thresholds de alerta
- ROI targets
- Savings plans strategy

## Deployment

### Docker
```bash
docker build -f Dockerfile -t finops-agent .
docker run -p 8003:8003 --env-file .env finops-agent
```

### Kubernetes
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: finops-agent
spec:
  replicas: 1
  template:
    spec:
      containers:
      - name: finops-agent
        image: <ECR>/agent-squad-finops:latest
        ports:
        - containerPort: 8003
        env:
        - name: ATHENA_BUCKET
          value: "s3://company-athena-kubecost"
```

## Escala

- **Min replicas**: 1
- **Max replicas**: 5
- **Cache TTL**: 1h (custos)
- **HPA**: CPU > 70%

## Exemplo de Uso

### Query Simples
```bash
curl -X POST http://localhost:8003/process \
  -H "Content-Type: application/json" \
  -d '{
    "input_text": "Quanto gastamos este mês?",
    "user_id": "user123",
    "session_id": "session456",
    "chat_history": []
  }'
```

### Query com Follow-up
```bash
# Primeira pergunta
curl -X POST http://localhost:8003/process \
  -d '{"input_text": "Mostre custos por namespace", ...}'

# Follow-up (agent lembra do contexto)
curl -X POST http://localhost:8003/process \
  -d '{"input_text": "Qual teve maior aumento?", ...}'
```

## MCP Servers

Não requer MCP servers externos. Usa:
- AWS Cost Explorer API (boto3)
- AWS Athena API (boto3) para Kubecost data
