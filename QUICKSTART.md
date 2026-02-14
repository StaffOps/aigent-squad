# Quick Start - Local Development

## 🚀 Setup in 3 Steps

### 1. Configure Credentials

```bash
# Copy the example
cp .env.example .env.local

# Edit with your credentials
vim .env.local
```

**Minimum required**:
```bash
# AWS (required - for Bedrock)
# Use ~/.aws/credentials (automatically mounted)

# GitLab (required for DevOps Agent)
GITLAB_TOKEN=glpat-your-read-only-token

# Slack (optional)
SLACK_BOT_TOKEN=xoxb-...
SLACK_SIGNING_SECRET=...
```

**How to get GitLab Token**:
1. GitLab → Settings → Access Tokens
2. Name: `agent-squad-readonly`
3. Scopes: `read_api`, `read_repository`
4. Role: `Reporter` (read-only)

### 2. Run Setup

```bash
chmod +x setup-local.sh
./setup-local.sh
```

The script will:
- ✅ Check Docker and AWS CLI
- ✅ Create DynamoDB table (correct schema: pk + sk)
- ✅ Build all images
- ✅ Start all 7 services
- ✅ Automatic health check

### 3. Test

```bash
# Test AWS Agent
curl -X POST http://localhost:8001/process \
  -H 'Content-Type: application/json' \
  -d '{
    "input_text": "List EC2 instances",
    "user_id": "test",
    "session_id": "test123",
    "chat_history": []
  }'

# Test Supervisor (with Classifier)
curl -X POST http://localhost:8000/query \
  -H 'Content-Type: application/json' \
  -d '{
    "user_input": "How many EC2 instances?",
    "user_id": "test",
    "session_id": "test123"
  }'
```

---

## 📊 Services

| Service | Port | URL | Health |
|---------|------|-----|--------|
| Supervisor | 8000 | http://localhost:8000 | /health |
| AWS Agent | 8001 | http://localhost:8001 | /health |
| Kubernetes Agent | 8002 | http://localhost:8002 | /health |
| FinOps Agent | 8003 | http://localhost:8003 | /health |
| DevOps Agent | 8004 | http://localhost:8004 | /health |
| Observability Agent | 8005 | http://localhost:8005 | /health |
| MCP Server | 8006 | http://localhost:8006 | /health |
| Redis | 6379 | localhost:6379 | - |
| DynamoDB Local | 8100 | http://localhost:8100 | - |

---

## 🔧 Useful Commands

### Logs
```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f supervisor
docker compose logs -f aws-agent

# Last 100 lines
docker compose logs --tail=100 aws-agent
```

### Restart
```bash
# Restart specific service (after changing prompt)
docker compose restart aws-agent

# Restart all
docker compose restart

# Rebuild and restart
docker compose up -d --build aws-agent
```

### Stop/Start
```bash
# Stop everything
docker compose down

# Stop and remove volumes
docker compose down -v

# Start everything
docker compose up -d

# Start with logs
docker compose up
```

### Debug
```bash
# Enter container
docker compose exec aws-agent sh

# View environment variables
docker compose exec aws-agent env | grep BEDROCK

# Test Redis
docker compose exec redis redis-cli ping

# Test DynamoDB
aws dynamodb list-tables --endpoint-url http://localhost:8100
```

---

## 🐛 Troubleshooting

### Agent doesn't start
```bash
# View logs
docker compose logs aws-agent

# Check if port is free
lsof -i :8001

# Rebuild
docker compose build aws-agent
docker compose up -d aws-agent
```

### AWS credentials error
```bash
# Check ~/.aws/credentials
cat ~/.aws/credentials

# Test AWS CLI
aws sts get-caller-identity

# Check mount in container
docker compose exec aws-agent ls -la /root/.aws/
```

### DynamoDB table error
```bash
# Recreate table
aws dynamodb delete-table --table-name agent-sessions --endpoint-url http://localhost:8100
aws dynamodb create-table \
  --table-name agent-sessions \
  --attribute-definitions \
      AttributeName=pk,AttributeType=S \
      AttributeName=sk,AttributeType=S \
  --key-schema \
      AttributeName=pk,KeyType=HASH \
      AttributeName=sk,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST \
  --endpoint-url http://localhost:8100
```

### GitLab integration doesn't work
```bash
# Check token
echo $GITLAB_TOKEN

# Test token
curl -H "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "https://gitlab.com/api/v4/projects/your-organization%2Fdevops"

# View DevOps Agent logs
docker compose logs -f devops-agent | grep -i gitlab
```

---

## 🔄 Development Workflow

### 1. Modify Prompt
```bash
vim src/agents/aws/prompt.md
docker compose restart aws-agent
# Test
curl -X POST http://localhost:8001/process ...
```

### 2. Modify Code
```bash
vim src/agents/aws/agent.py
docker compose up -d --build aws-agent
# Test
curl -X POST http://localhost:8001/process ...
```

### 3. View Real-time Logs
```bash
docker compose logs -f aws-agent | jq .
```

### 4. Test Conversation History
```bash
# First message
curl -X POST http://localhost:8000/query \
  -d '{"user_input":"Show EC2","user_id":"test","session_id":"abc123"}'

# Follow-up (classifier should keep AWS agent)
curl -X POST http://localhost:8000/query \
  -d '{"user_input":"How many in us-east-1?","user_id":"test","session_id":"abc123"}'
```

---

## 📝 Notes

- **DynamoDB Local**: In-memory data, lost on restart
- **Redis**: Data persisted in `redis-data` volume
- **AWS Credentials**: Mounted read-only from `~/.aws`
- **JSON Logs**: Use `jq` to format: `docker compose logs -f | jq .`
- **OpenTelemetry**: Console exporter active, traces in logs

---

## 🚀 Next Steps

1. ✅ Local setup working
2. Test all 5 agents
3. Test Supervisor with Classifier
4. Test conversation history
5. Test GitLab integration (DevOps Agent)
6. Test MCP Server with Kiro CLI
7. Deploy on EKS
8. Integrate with Slack

---

## 🔌 MCP Server (Kiro CLI Integration)

### Setup

```bash
# Add to ~/.kiro/mcp.json
{
  "mcpServers": {
    "agent-squad": {
      "url": "http://localhost:8006/query"
    }
  }
}
```

### Test

```bash
# Health check
curl http://localhost:8006/health

# Query
curl -X POST http://localhost:8006/query \
  -H 'Content-Type: application/json' \
  -d '{"question": "How many EC2 instances?", "user_id": "test"}'
```

### Usage

```bash
kiro-cli chat

# Ask naturally
> How many EC2 instances are running?
# Kiro uses MCP server automatically

> What is the cost of namespace production?
# Routed to FinOps Agent
```

See complete documentation: `docs/MCP_INTEGRATION.md`

---

**Version**: 2.0  
**Date**: 2026-02-14
