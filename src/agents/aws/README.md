# AWS Agent

Especialista em recursos AWS (read-only) com suporte a conversação contextual.

## Função

- Analisa recursos AWS (EC2, RDS, S3, Lambda, etc)
- Identifica problemas e oportunidades
- Sugere otimizações via Terraform
- **Mantém contexto conversacional** por usuário/sessão
- **100% read-only** - nunca modifica recursos

## Dependências

### AWS
- **Bedrock**: Claude 3.5 Sonnet (Converse API)
- **AWS APIs**: EC2, RDS, S3, Lambda, ECS
- **DynamoDB**: Conversation history (gerenciado pelo Supervisor)

### Infraestrutura
- **Redis**: Cache de inventários (TTL 5min)

## Environment Variables

```bash
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=anthropic.claude-sonnet-4-5-20250929-v1:0
REDIS_HOST=<endpoint>
REDIS_PORT=6379
REDIS_SSL=true
REDIS_PASSWORD=<optional>
```

## IAM Permissions

```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock:InvokeModel",
    "ec2:Describe*",
    "rds:Describe*",
    "s3:List*",
    "s3:GetBucketLocation",
    "lambda:List*",
    "lambda:Get*",
    "ecs:Describe*"
  ],
  "Resource": "*"
},
{
  "Effect": "Deny",
  "Action": ["*:Create*", "*:Delete*", "*:Update*", "*:Modify*"],
  "Resource": "*"
}
```

## API Endpoints

### `POST /process`
Processa query AWS com conversation history.

**Request**:
```json
{
  "input_text": "Quantas instâncias EC2 estão rodando?",
  "user_id": "user123",
  "session_id": "session456",
  "chat_history": [
    {
      "role": "user",
      "content": "Mostre os buckets S3",
      "timestamp": "2026-02-14T10:00:00Z"
    },
    {
      "role": "assistant",
      "content": "Você tem 5 buckets...",
      "timestamp": "2026-02-14T10:00:05Z"
    }
  ],
  "additional_params": {}
}
```

**Response**:
```json
{
  "role": "assistant",
  "content": "Você tem 12 instâncias EC2 rodando...",
  "timestamp": "2026-02-14T10:01:00Z",
  "agent_id": "aws"
}
```

### `GET /health`
Health check.

**Response**:
```json
{
  "status": "healthy",
  "agent": "aws"
}
```

## Conversation History

O agent recebe **apenas seu próprio histórico** (isolado de outros agents):
- Mantém contexto de conversas anteriores sobre AWS
- Não vê conversas com Kubernetes, FinOps, etc
- Histórico gerenciado automaticamente pelo Supervisor

## Customização

Edite `prompt.md` para adicionar:
- Naming conventions da empresa
- Políticas internas
- Tags obrigatórias
- Accounts e regiões específicas

## Deployment

### Docker
```bash
docker build -f Dockerfile -t aws-agent .
docker run -p 8001:8001 --env-file .env aws-agent
```

### Kubernetes
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: aws-agent
spec:
  replicas: 3
  template:
    spec:
      containers:
      - name: aws-agent
        image: <ECR>/agent-squad-aws:latest
        ports:
        - containerPort: 8001
        env:
        - name: AWS_REGION
          value: "us-east-1"
        - name: REDIS_HOST
          valueFrom:
            secretKeyRef:
              name: agent-squad-secrets
              key: redis-host
```

## Escala

- **Min replicas**: 3
- **Max replicas**: 15
- **Cache TTL**: 5min (inventários)
- **HPA**: CPU > 70%

## Exemplo de Uso

### Query Simples
```bash
curl -X POST http://localhost:8001/process \
  -H "Content-Type: application/json" \
  -d '{
    "input_text": "Quais EC2 estão idle?",
    "user_id": "user123",
    "session_id": "session456",
    "chat_history": []
  }'
```

### Query com Contexto (Follow-up)
```bash
# Primeira pergunta
curl -X POST http://localhost:8001/process \
  -d '{"input_text": "Mostre instâncias EC2", "user_id": "user123", "session_id": "session456", "chat_history": []}'

# Follow-up (agent lembra do contexto)
curl -X POST http://localhost:8001/process \
  -d '{"input_text": "Quais estão em us-east-1?", "user_id": "user123", "session_id": "session456", "chat_history": [...]}'
```

## MCP Servers

Não requer MCP servers externos. Usa APIs AWS nativas via boto3.
