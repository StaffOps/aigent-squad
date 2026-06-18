# Setup Guide

## Local Development

### Prerequisites
- Docker + Docker Compose
- SSH key configured for GitHub (for private otel-helper repo)
- AWS credentials (`~/.aws/`) — optional, agents degrade gracefully without them

### Quick start

```bash
# 1. Clone
git clone git@github.com:StaffOps/staffops-aigent-squad.git
cd staffops-aigent-squad

# 2. Ensure ssh-agent is running (for private dep install during build)
eval $(ssh-agent -s)
ssh-add ~/.ssh/id_ed25519

# 3. Build + run
docker compose build
docker compose up -d

# 4. Verify
curl http://localhost:8000/healthz  # liveness
curl http://localhost:8000/ready    # readiness (Redis + DynamoDB + agents)
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

### Image

The image is published to Docker Hub on every merge to `main`:

```bash
docker pull karlipegomes/aigent-squad:latest
```

Tags: `latest` + `sha-<short>`. Multi-arch manifest (amd64 + arm64).

### Deploy via Helm

```bash
# Add the chart repo (published via GitHub Pages)
helm repo add staffops https://StaffOps.github.io/helm-charts
helm repo update

# Install (inProcess topology — one pod, all agents in-process)
helm install aigent-squad staffops/aigent-squad \
  --namespace aigent-squad --create-namespace \
  --set global.image.registry="" \
  --set services.supervisor.image.repository=karlipegomes/aigent-squad \
  --set services.supervisor.image.tag=latest \
  --set redis.host=my-elasticache.cache.amazonaws.com
```

See `helm-charts/charts/aigent-squad/README.md` for full values reference.

### CI/CD

Pipeline runs on GitHub Actions (`.github/workflows/`):
- **test.yml**: lint (ruff) + pytest `--cov-fail-under=90` (≥90% enforced)
- **build.yml**: multi-arch Docker build → ECR (OIDC) + Docker Hub (`karlipegomes/aigent-squad`) + Trivy scan + SBOM
- **helm-charts repo**: `release.yaml` (chart-releaser) + `lint-test.yaml` (ct lint + kind install)
