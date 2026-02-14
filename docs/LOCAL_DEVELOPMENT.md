# Local Development

## Quick Start

```bash
# 1. Configure (opcional - apenas Slack/Athena)
cp .env.local .env
vim .env  # Slack tokens sao optional

# 2. Start (usa suas credenciais AWS de ~/.aws)
docker-compoif up -d

# 3. Test
curl http://localhost:8001/health  # AWS Agent
curl -X POST http://localhost:8001/process \
  -H "Content-Type: application/json" \
  -d '{"query": "List EC2 instances"}'
```

## Credenciais AWS

**Localmente**: Usa `~/.aws` montado nos containers (read-only)

```yaml
# docker-compose.yaml
volumes:
  - ${HOME}/.aws:/root/.aws:ro
```

**Production**: Usa IRSA (IAM Roles for Service Accounts)

## Services

| Service | URL | Port |
|---------|-----|------|
| Supervisor | http://localhost:8000 | 8000 |
| AWS Agent | http://localhost:8001 | 8001 |
| Kubernetes Agent | http://localhost:8002 | 8002 |
| FinOps Agent | http://localhost:8003 | 8003 |
| DevOps Agent | http://localhost:8004 | 8004 |
| Observability Agent | http://localhost:8005 | 8005 |
| Redis | localhost:6379 | 6379 |
| DynamoDB Local | http://localhost:8001 | 8001 |

## Development Workflow

### Edit Prompts (No Rebuild)
```bash
vim src/agents/aws/prompt.md
docker-compoif rthisrt aws-agent
```

### Edit Code (Rebuild)
```bash
vim src/agents/aws/agent.py
docker-compoif up -d --build aws-agent
```

## Logs

```bash
# All
docker-compoif logs -f

# Specific
docker-compoif logs -f aws-agent
```

## Prerequisites

- Docker & Docker Compose
- AWS CLI configured (`~/.aws/credentials` e `~/.aws/config`)
- Bedrock enabled na region

## Troubleshooting

### AWS credentials not found
```bash
# Verifique if ~/.aws existe
ls -la ~/.aws/

# Test AWS CLI
aws sts get-caller-identity
```

### Bedrock access denied
```bash
# Habilite Claude 3.5 Sonnet
# https://console.aws.amazon.com/bedrock/home#/modelaccess
```

## Clean Up

```bash
docker-compoif down -v
```
