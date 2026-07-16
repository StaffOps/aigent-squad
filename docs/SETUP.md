# Setup Guide

## Local Development

### Prerequisites
- Docker + Docker Compose
- AWS credentials with Bedrock access (`~/.aws/`) — needed for real agent
  answers (every query is a live Bedrock call, no offline/fixture mode
  today). NOT needed for `make lint` / `make test`, which run entirely in
  Docker with no AWS involved. See `docs/PREREQUISITES.md` for exactly what
  a live demo does (and doesn't) require — short version: no Terraform, no
  IRSA, no EKS, just a Bedrock-capable credential.

### Quick start

```bash
# 1. Clone
git clone git@github.com:StaffOps/staffops-aigent-squad.git
cd staffops-aigent-squad

# 2. Build + run (make handles Docker build/up/health-wait — see AGENTS.md)
make up
make smoke   # health + 1 real query + /v1/models

# 3. Verify manually if you want
curl http://localhost:8000/healthz  # gateway liveness
curl http://localhost:8000/ready    # gateway readiness (supervisor + deps)
curl http://localhost:3001          # Grafana dashboards
```

### Services

| Service | URL | Purpose |
|---------|-----|---------|
| Gateway | http://localhost:8000 | Public front door — `/query`, `/v1/*`, health |
| Supervisor | http://localhost:8001 | Backend-only — `/internal/*` (not for direct client use) |
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
- A container registry (Docker Hub is the CI target by default; ECR/Harbor
  also work — see `helm-charts/charts/aigent-squad/README.md`)

### Image

The image is published to Docker Hub (`karlipegomes/aigent-squad`) on every
merge to `main` (`latest` + `sha-<short>` tags) and on every version tag
(`vX.Y.Z` → `X.Y.Z` + a GitHub Release). Multi-arch manifest (amd64 + arm64).

```bash
docker pull karlipegomes/aigent-squad:latest
```

### Deploy via Helm

```bash
# Add the chart repo (published via GitHub Pages)
helm repo add staffops https://StaffOps.github.io/helm-charts
helm repo update

# Install (inProcess topology — one release, gateway + supervisor)
helm install aigent-squad staffops/aigent-squad \
  --namespace aigent-squad --create-namespace \
  --set redis.host=my-elasticache.cache.amazonaws.com
```

`global.image.registry` defaults to `""` (Docker Hub as-is) and
`services.{gateway,supervisor}.image.repository` already default to
`karlipegomes/aigent-squad`, versioned by `Chart.appVersion` — no image
override needed for a vanilla install. See
`helm-charts/charts/aigent-squad/README.md` for the full values reference.

### CI/CD

Pipeline runs on GitHub Actions (`.github/workflows/`):
- **test.yml**: lint (ruff) + pytest `--cov-fail-under=90` (≥90% enforced)
- **build.yml**: multi-arch Docker build → Docker Hub (`karlipegomes/aigent-squad`, `latest`+SHA tags) + Trivy scan + SBOM, on every merge to `main`
- **release.yml**: same build, triggered by a `vX.Y.Z` tag — publishes the version tag + a GitHub Release (see `RELEASE.md`)
- **helm-charts repo**: `release.yaml` (chart-releaser) + `lint-test.yaml` (ct lint + kind install)
