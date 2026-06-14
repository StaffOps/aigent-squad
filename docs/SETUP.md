# Setup Guide

## Local Development

### Prerequisites
- Docker + Docker Compose
- SSH key configured for GitHub (for private otel-helper repo)
- AWS credentials (`~/.aws/`) — optional, agents degrade gracefully without them

### Quick start

```bash
# 1. Clone
git clone git@github.com:karlipegomes/AIgent-squad.git
cd AIgent-squad

# 2. Ensure ssh-agent is running (for private dep install during build)
eval $(ssh-agent -s)
ssh-add ~/.ssh/id_ed25519

# 3. Build + run
docker compose build
docker compose up -d

# 4. Verify
curl http://localhost:8000/health  # supervisor
curl http://localhost:3001         # Grafana dashboards
```

### Services

| Service | URL | Purpose |
|---------|-----|---------|
| Supervisor | http://localhost:8000 | Main API (query routing) |
| MCP Server | http://localhost:8006 | Kiro CLI integration |
| Grafana | http://localhost:3001 | Dashboards + traces |
| Prometheus | http://localhost:9099 | Metrics |

### Making queries

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: dev-secret-token" \
  -d '{"user_input":"list ec2 instances","user_id":"dev","session_id":"test"}'
```

### Adding a new agent

See [HOW-TO-NEW-AGENT.md](HOW-TO-NEW-AGENT.md).

---

## Production (Kubernetes)

### Prerequisites
- EKS cluster with IRSA configured
- Helm 3.x
- AWS ECR or Harbor registry

### Deploy via Helm

```bash
helm install aigent-squad oci://your-registry/charts/aigent-squad \
  --set image.tag=v0.1.0 \
  --set agentsSource.type=git \
  --set agentsSource.repo=https://github.com/your-org/agent-definitions.git \
  --set agentsSource.tokenSecret=git-token \
  --set env.AWS_REGION=us-east-1 \
  --set env.INTERNAL_API_TOKEN=your-prod-token
```

See the Helm chart at `helm-charts/charts/aigent-squad/` for full values reference.

### CI/CD

Pipeline runs on GitHub Actions (`.github/workflows/`):
- **test.yml**: lint (ruff) + pytest --cov-fail-under=80
- **build.yml**: multi-arch Docker build + Trivy scan + SBOM + push to ECR (OIDC)
- **release.yml**: manual tag `v<semver>` + stable image push
