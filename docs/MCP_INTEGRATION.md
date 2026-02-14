# MCP Server Integration

## Overview

O Agent Squad exposes um **MCP (Model Context Protocol) Server** que permite integration com o Kiro CLI e outros clientes MCP.

## Arquitetura

```
+-------------+
|  Kiro CLI   |
+------+------+
       | HTTP POST
       ?
+-----------------+
|  MCP Server     |
|  (port 8006)    |
+------+----------+
       | HTTP
       ?
+-----------------+
|   Supervisor    |
|   (port 8000)   |
+------+----------+
       |
       ?
+-------------------------------------+
|  Specialist Agents (8001-8005)      |
|  AWS | K8s | FinOps | DevOps | Obs  |
+-------------------------------------+
```

## Endpoints

### `POST /query`

Envia perguntas for o Agent Squad e recebe respostas dos agentes especialistas.

**Request**:
```json
{
  "question": "How many instances EC2 are running em us-east-1?",
  "user_id": "john.doe"
}
```

**Response**:
```json
{
  "agent": "aws",
  "response": "Encontrei 15 instances EC2 em us-east-1...",
  "confidence": 0.95,
  "reasoning": "Question mentions EC2 and AWS region"
}
```

### `GET /health`

Health check endpoint.

**Response**:
```json
{
  "status": "healthy"
}
```

## Setup Local

### 1. Start Agent Squad

```bash
cd code/
docker-compoif up -d
```

Aguarde all os services ficarem healthy (~30s).

### 2. Configure Kiro CLI

Adicione no `~/.kiro/mcp.json`:

```json
{
  "mcpServers": {
    "agent-squad": {
      "url": "http://localhost:8006/query"
    }
  }
}
```

### 3. Usar no Kiro CLI

```bash
kiro-cli chat

# Pergunte naturalmente
> How many instances EC2 are running?
# Kiro detecta automatically e usa o MCP server

> What is the cost do namespace production?
# Roteado for FinOps Agent

> Show pods com rthisrt count alto
# Roteado for Kubernetes Agent
```

## Conversas Multi-Turn

O MCP server maintains conversation context:

```bash
> List buckets S3 em us-east-1
# AWS Agent responde

> Which them have versioning disabled?
# Classifier detecta follow-up, maintains AWS Agent
```

## Troubleshooting

### MCP Server not responde

```bash
# Check if supervisor esta healthy
docker-compoif ps supervisor

# Ver logs do MCP server
docker-compoif logs mcp-server

# Check conectividade
docker exec -it agent-squad-mcp-server-1 wget -O- http://supervisor:8000/health
```

### Timeout

O MCP server tem timeout de 30s. Para perguntas complexas que demoram more:

```bash
# Aumentar timeout no mcp-server.py
async with httpx.AsyncClient(timeout=60.0) as client:
```

### Erro de permissao

```bash
# Check if AWS credentials estao montadas
docker-compoif exec supervisor ls -la /root/.aws

# Check variaveis de ambiente
docker-compoif exec supervisor env | grep AWS
```

## Desenvolvimento

### Tthisr MCP Server Localmente

```bash
# Health check
curl http://localhost:8006/health

# Query Agent Squad
curl -X POST http://localhost:8006/query \
  -H 'Content-Type: application/json' \
  -d '{
    "question": "How many instances EC2 em us-east-1?",
    "user_id": "test"
  }'
```

### Add New Ferramenta

Edite `mcp-server.py`:

```python
@app.post("/new_ferramenta")
async def new_ferramenta(request: NewRequest):
    # Implementation
    pass
```

## Seguranca

- MCP server e **read-only** (apenas consulta)
- Nao exposes credenciais AWS
- Timeout de 30s previne DoS
- Validation de input no supervisor

## Performance

- Latencia tipica: 2-5s (depende do agent)
- Suporta conversas concorrentes (stateless)
- Cache Redis reduz latencia em consultas repetidas

## Next Steps

- [ ] Add streaming de respostas
- [ ] Suportar multiples agents em forlelo
- [ ] Add metrics (OpenTelemetry)
- [ ] Deploy do MCP server on EKS
