# Setup Guide

## Prerequisites

- AWS Account com Bedrock enabled
- EKS cluster existente
- kubectl configured
- Terraform >= 1.0
- GitLab CI ou Docker

## 1. Deploy Infraestrutura (10min)

```bash
cd terraform

# Configure
cp terraform.tfvars.example terraform.tfvars
vim terraform.tfvars  # Adicione eks_cluster_name

# Deploy
terraform init
terraform apply

# Anote outputs
terraform output redis_endpoint
terraform output iam_role_arn
```

## 2. Build Imagens (5min)

### Option A: GitLab CI (Recommended)

```bash
# Configure variables no GitLab:
# Settings -> CI/CD -> Variables
AWS_ACCOUNT_ID=123456789012
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=***

# Push for trigger pipeline
git push origin main
```

### Option B: Local

```bash
cd code
docker-compoif build
# Push manual for ECR
```

## 3. Deploy Kubernetes (5min)

```bash
cd k8s_manifests

# Secrets
kubectl create secret generic agent-squad-secrets \
  --from-literal=redis-host=<REDIS_ENDPOINT> \
  --from-literal=slack-bot-token=<TOKEN> \
  --from-literal=slack-signing-secret=<SECRET>

# Deploy
kubectl apply -f rbac.yaml
kubectl apply -f agents/
kubectl apply -f deployment.yaml
kubectl apply -f ingress.yaml
kubectl apply -f cronjobs.yaml

# Check
kubectl get pods
kubectl get hpa
```

## 4. Configure Slack (5min)

1. **Create App**: https://api.slack.com/apps -> "Create New App"
2. **Scopes**: OAuth & Permissions -> Add:
   - `app_mentions:read`
   - `chat:write`
3. **Events**: Event Subscriptions -> Enable
   - URL: `https://your-domain.com/slack/events`
   - Subscribe: `app_mention`
4. **Install**: Install to Workspace
5. **Tokens**: Copie Bot Token e Signing Secret

## 5. Tthisr

```bash
# Slack
@Agent Squad which EC2 are running?

# Logs
kubectl logs -l app=agent-squad-supervisor -f
```

## Troubleshooting

### Pods not iniciam
```bash
kubectl describe pod <pod-name>
kubectl logs <pod-name>
```

### Slack not responde
```bash
# Test endpoint
curl https://your-domain.com/health

# Verifique ingress
kubectl get ingress
```

### Bedrock errors
- Habilite Claude 3.5 Sonnet na region: https://console.aws.amazon.com/bedrock/home#/modelaccess

## Next Steps

1. Customize prompts: `code/src/agents/*/prompt.md`
2. Ajuste HPA: `k8s_manifests/agents/*.yaml`
3. Configure alertas proativos: `k8s_manifests/cronjobs.yaml`
