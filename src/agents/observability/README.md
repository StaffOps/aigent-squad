# Observability Agent

Especialista em métricas, logs e anomalias (read-only) com suporte a conversação contextual.

## Função

- Analisa métricas (Prometheus, CloudWatch)
- Detecta anomalias
- Correlaciona logs e traces
- Sugere ajustes via Git (alerts, dashboards)
- **Mantém contexto conversacional** por usuário/sessão
- **100% read-only** - nunca modifica alerts ou silencia notificações

## Dependências

### AWS
- **Bedrock**: Claude 3.5 Sonnet (Converse API)
- **CloudWatch**: Métricas AWS (opcional)
- **DynamoDB**: Conversation history (gerenciado pelo Supervisor)

### Infraestrutura
- **Redis**: Cache de métricas (TTL 1min)
- **Prometheus**: Métricas K8s (opcional)
- **Grafana**: Dashboards (opcional)

## Environment Variables

```bash
# AWS
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=anthropic.claude-sonnet-4-5-20250929-v1:0

# Redis
REDIS_HOST=<endpoint>
REDIS_PORT=6379
REDIS_SSL=true

# Opcional: Prometheus
PROMETHEUS_URL=http://prometheus.monitoring.svc.cluster.local:9090

# Opcional: Grafana
GRAFANA_URL=https://grafana.company.com
GRAFANA_TOKEN=<token>
```

## IAM Permissions

```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock:InvokeModel",
    "cloudwatch:GetMetricStatistics",
    "cloudwatch:ListMetrics",
    "cloudwatch:GetMetricData",
    "logs:FilterLogEvents",
    "logs:GetLogEvents"
  ],
  "Resource": "*"
},
{
  "Effect": "Deny",
  "Action": ["*:Put*", "*:Create*", "*:Update*", "*:Delete*"],
  "Resource": "*"
}
```

## API Endpoints

### `POST /process`
Processa query Observability com conversation history.

**Request**:
```json
{
  "input_text": "Detectar anomalias nas últimas 2h",
  "user_id": "user123",
  "session_id": "session456",
  "chat_history": [
    {
      "role": "user",
      "content": "Mostre métricas de CPU",
      "timestamp": "2026-02-14T10:00:00Z"
    },
    {
      "role": "assistant",
      "content": "CPU média está em 45%...",
      "timestamp": "2026-02-14T10:00:05Z"
    }
  ]
}
```

**Response**:
```json
{
  "role": "assistant",
  "content": "Detectei 2 anomalias nas últimas 2h:\n1. Spike de CPU no pod X às 08:30...",
  "timestamp": "2026-02-14T10:01:00Z",
  "agent_id": "observability"
}
```

### `GET /health`
Health check.

## Conversation History

O agent recebe **apenas seu próprio histórico** (isolado de outros agents):
- Mantém contexto de conversas anteriores sobre observability
- Não vê conversas com AWS, Kubernetes, etc
- Histórico gerenciado automaticamente pelo Supervisor

## Anomaly Detection

Compara métricas atuais com baseline histórico:

### Algoritmo
```python
# 1. Busca baseline (últimos 7 dias, mesmo horário)
baseline = get_metric_baseline(metric_name, window="7d")

# 2. Calcula desvio padrão
std_dev = calculate_std_dev(baseline)

# 3. Detecta anomalias (> 3 sigma)
if current_value > (baseline_mean + 3 * std_dev):
    alert_anomaly()
```

### Métricas Monitoradas
- CPU/Memory spikes
- Latency degradation (p95, p99)
- Error rate increase (5xx)
- Request rate anomalies
- Disk I/O saturation

## Prometheus Integration

### Query Examples
```promql
# CPU usage por pod
rate(container_cpu_usage_seconds_total[5m])

# Memory usage
container_memory_working_set_bytes

# Request rate
rate(http_requests_total[5m])

# Error rate
rate(http_requests_total{status=~"5.."}[5m])
```

## CloudWatch Integration

### Metrics Examples
```python
# Lambda errors
cloudwatch.get_metric_statistics(
    Namespace='AWS/Lambda',
    MetricName='Errors',
    Dimensions=[{'Name': 'FunctionName', 'Value': 'my-function'}],
    StartTime=start,
    EndTime=end,
    Period=300,
    Statistics=['Sum']
)
```

## Customização

Edite `prompt.md` para adicionar:
- Stack de observabilidade (Prometheus, Grafana, Datadog, New Relic)
- SLIs/SLOs definidos
- Thresholds de alerta
- Runbooks de troubleshooting
- Dashboards importantes

## Deployment

### Docker
```bash
docker build -f Dockerfile -t observability-agent .
docker run -p 8005:8005 --env-file .env observability-agent
```

### Kubernetes
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: observability-agent
spec:
  replicas: 2
  template:
    spec:
      containers:
      - name: observability-agent
        image: <ECR>/agent-squad-observability:latest
        ports:
        - containerPort: 8005
        env:
        - name: PROMETHEUS_URL
          value: "http://prometheus.monitoring.svc.cluster.local:9090"
```

## Escala

- **Min replicas**: 2
- **Max replicas**: 8
- **Cache TTL**: 1min (métricas)
- **HPA**: CPU > 70%

## Exemplo de Uso

### Query Simples
```bash
curl -X POST http://localhost:8005/process \
  -H "Content-Type: application/json" \
  -d '{
    "input_text": "Mostre erros 5xx nas últimas 2h",
    "user_id": "user123",
    "session_id": "session456",
    "chat_history": []
  }'
```

### Query com Follow-up
```bash
# Primeira pergunta
curl -X POST http://localhost:8005/process \
  -d '{"input_text": "Detectar anomalias", ...}'

# Follow-up (agent lembra do contexto)
curl -X POST http://localhost:8005/process \
  -d '{"input_text": "Qual foi a causa?", ...}'
```

## MCP Servers

### Opcional: Prometheus MCP
Se você tem um MCP server para Prometheus:

```yaml
# mcp-server-prometheus.yaml
name: prometheus
endpoint: http://prometheus-mcp:8080
tools:
  - query_metric
  - get_alerts
  - list_targets
```

O agent descobrirá e usará automaticamente.
