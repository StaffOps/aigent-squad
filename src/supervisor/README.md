# Supervisor Agent

Orchestrates and delegates tasks to specialists using an **intelligent
Classifier** and conversation history.

## Role

- Receives requests from Slack (or direct API)
- **Classifies intent** with AI (Bedrock Claude)
- Detects follow-ups and context switching
- Delegates to the appropriate specialist via HTTP
- Keeps per-agent isolated conversation context
- Consolidates responses

## Architecture

```
User → Supervisor → Classifier (AI) → Selects Agent
                  ↓
            DynamoDB (conversation history)
                  ↓
            HTTP call → Specialist Agent
                  ↓
            Saves response → DynamoDB
                  ↓
            Returns to the user
```

### Changes vs v1.0
- ❌ **Removed**: LangGraph manual routing
- ✅ **Added**: intelligent AI Classifier
- ✅ **Added**: automatic follow-up detection
- ✅ **Added**: intelligent context switching
- ✅ **Added**: separate conversation history (global vs isolated)

## Dependencies

### AWS
- **Bedrock**: Claude 3.5 Sonnet (Classifier + Agents)
- **DynamoDB**: `agent-sessions` table (conversation history)

### Infrastructure
- **Redis**: Cache (optional)
- **Agents**: HTTP endpoints of the 5 specialists
  - AWS Agent: `http://aws-agent:8001/process`
  - Kubernetes Agent: `http://kubernetes-agent:8002/process`
  - FinOps Agent: `http://finops-agent:8003/process`
  - DevOps Agent: `http://devops-agent:8004/process`
  - Observability Agent: `http://observability-agent:8005/process`

### Kubernetes
- **ServiceAccount**: `agent-squad-supervisor` with IRSA
- **Secrets**: Slack tokens, DynamoDB access

## Environment Variables

```bash
# AWS
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-5-20250929-v1:0

# DynamoDB
DYNAMODB_SESSIONS_TABLE=agent-sessions
DYNAMODB_ENDPOINT=  # Optional: for local development

# Redis (optional)
REDIS_HOST=<endpoint>
REDIS_PORT=6379
REDIS_SSL=true

# Slack (optional)
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
  "content": "How many EC2 instances?",
  "timestamp": "2026-02-14T10:00:00Z",
  "ttl": 1739548800
}
```

## API Endpoints

### `POST /query`
Processes a user query with intelligent routing.

**Request**:
```json
{
  "user_input": "How many EC2 instances are running?",
  "user_id": "user123",
  "session_id": "session456"
}
```

**Response**:
```json
{
  "agent": "aws",
  "response": "You have 12 EC2 instances running...",
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

### How It Works

1. **Fetch global history** (all user/session conversations)
2. **Analyze with AI**:
   - User input
   - Agent descriptions
   - Conversation history
3. **Detect**:
   - Follow-ups ("yes", "ok", "1") → keep the same agent
   - Context switching ("now about costs") → switch agent
4. **Return**:
   - `selected_agent`: "aws" | "kubernetes" | "finops" | "devops" | "observability"
   - `confidence`: 0.0 - 1.0
   - `reasoning`: explanation of the decision

### Agent Descriptions (used by the Classifier)
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

### Two Levels

1. **Global** (the Classifier sees):
   - All user/session conversations
   - All agents
   - Used for classification

2. **Isolated** (the Agent sees):
   - Only conversations with that specific agent
   - Does not see conversations with other agents
   - Used to process the request

### Example
```
User: "How many EC2 instances?" → AWS Agent
User: "And pods?" → Kubernetes Agent
User: "Back to EC2" → AWS Agent (classifier detects context switch)
User: "How many in us-east-1?" → AWS Agent (follow-up, keeps the agent)
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

## Scaling

- **Min replicas**: 2
- **Max replicas**: 10
- **HPA**: CPU > 70% or Memory > 80%
- **Request timeout**: 30s (HTTP calls to agents)

## Usage Example

### Simple Query
```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "user_input": "How many EC2 instances?",
    "user_id": "user123",
    "session_id": "session456"
  }'
```

### Multi-turn Conversation
```bash
# 1. First question (AWS)
curl -X POST http://localhost:8000/query \
  -d '{"user_input": "Show EC2 instances", "user_id": "user123", "session_id": "session456"}'
# → Classifier selects: aws

# 2. Follow-up (keeps AWS)
curl -X POST http://localhost:8000/query \
  -d '{"user_input": "How many in us-east-1?", "user_id": "user123", "session_id": "session456"}'
# → Classifier detects follow-up, keeps: aws

# 3. Context switch (switches to FinOps)
curl -X POST http://localhost:8000/query \
  -d '{"user_input": "How much do they cost?", "user_id": "user123", "session_id": "session456"}'
# → Classifier detects topic change, selects: finops

# 4. Follow-up (keeps FinOps)
curl -X POST http://localhost:8000/query \
  -d '{"user_input": "And last month?", "user_id": "user123", "session_id": "session456"}'
# → Classifier detects follow-up, keeps: finops
```

## Slack Integration (Optional)

### Configuration
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

Does not require MCP servers. Uses:
- Bedrock API directly (boto3)
- DynamoDB API (boto3)
- HTTP calls to specialist agents
