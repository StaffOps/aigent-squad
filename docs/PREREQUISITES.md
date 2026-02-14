# Prerequisites

## Infraestrutura (Terraform)

### DynamoDB
- Table: `agent-squad-sessions`
- Hash key: `session_id`
- TTL enabled

### ElastiCache
- Redis Serverless
- Endpoint accessible do EKS

### IAM
- Role com IRSA for ServiceAccount `agent-squad`
- Permissions: Bedrock, DynamoDB, AWS read-only, Athena
- Explicit Deny em writes

### ECR
- 6 repositories:
  - agent-squad-supervisor
  - agent-squad-aws
  - agent-squad-kubernetes
  - agent-squad-finops
  - agent-squad-devops
  - agent-squad-observability

## AWS

### Bedrock
- Claude 3.5 Sonnet enabled na region
- https://console.aws.amazon.com/bedrock/home#/modelaccess

### Kubecost (FinOps Agent)
- Athena database: `kubecost`
- Table: `kubecost_split`
- Bucket: `s3://company-athena-kubecost`

## Kubernetes

### Cluster
- EKS existente
- OIDC provider enabled

### Secrets
```bash
kubectl create secret generic agent-squad-secrets \
  --from-literal=redis-host=<REDIS_ENDPOINT> \
  --from-literal=slack-bot-token=<TOKEN> \
  --from-literal=slack-signing-secret=<SECRET>
```

## Slack

### App Configuration
1. Create app: https://api.slack.com/apps
2. Scopes: `app_mentions:read`, `chat:write`
3. Event subscription: `app_mention`
4. Webhook URL: `https://your-domain.com/slack/events`

## Environment Variables

### All os Agents
```bash
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=anthropic.claude-3-5-sonnet-20241022-v2:0
REDIS_HOST=<endpoint>
REDIS_PORT=6379
REDIS_SSL=true
```

### Supervisor
```bash
DYNAMODB_SESSIONS_TABLE=agent-sessions
SLACK_BOT_TOKEN=xoxb-...
SLACK_SIGNING_SECRET=...
SLACK_PROACTIVE_CHANNEL=C12345678
```

### FinOps Agent
```bash
ATHENA_PROJECT_ID=123456789012
ATHENA_BUCKET=s3://company-athena-kubecost
ATHENA_DATABASE=kubecost
ATHENA_TABLE=kubecost_split
```

### DevOps Agent (Optional)
```bash
DOCS_PORTAL_URL=https://docs.company.com
DOCS_PORTAL_TOKEN=...
```

### Observability Agent (Optional)
```bash
PROMETHEUS_URL=http://prometheus:9090
```
