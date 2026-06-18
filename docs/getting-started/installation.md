# Installation

## Local development

### 1. Clone

```bash
git clone git@github.com:StaffOps/staffops-aigent-squad.git
cd staffops-aigent-squad
```

### 2. Load SSH key (required for private dep)

```bash
eval $(ssh-agent -s)
ssh-add ~/.ssh/id_ed25519
```

### 3. Start the stack

```bash
docker compose up -d
```

This builds the image (using your SSH agent for the private `otel-helper` dep) and starts:

| Service | URL | Purpose |
|---------|-----|---------|
| Supervisor | http://localhost:8000 | Main API |
| MCP Server | http://localhost:8006 | Kiro CLI integration |
| Redis | localhost:6379 | Agent data cache |
| DynamoDB Local | localhost:8001 | Conversation history |

### 4. Verify

```bash
curl http://localhost:8000/healthz   # liveness — always 200
curl http://localhost:8000/ready     # readiness — checks Redis + DynamoDB + agents
```

---

## Kubernetes (Helm)

### Add the chart repo

```bash
helm repo add staffops https://staffops.github.io/helm-charts/
helm repo update
```

### Install (inProcess topology — single pod, all agents in-process)

```bash
helm install aigent-squad staffops/aigent-squad \
  --namespace aigent-squad --create-namespace \
  --set redis.host=my-cluster.cache.amazonaws.com \
  --set services.supervisor.serviceAccount.annotations."eks\.amazonaws\.com/role-arn"=arn:aws:iam::ACCOUNT:role/aigent-squad
```

### Install (distributed topology — separate pod per agent)

```bash
helm install aigent-squad staffops/aigent-squad \
  --namespace aigent-squad --create-namespace \
  -f https://raw.githubusercontent.com/StaffOps/helm-charts/main/charts/aigent-squad/values-distributed.yaml \
  --set redis.host=my-cluster.cache.amazonaws.com
```

See [Helm Reference](../reference/helm.md) for the full values schema.

---

## Running tests

Tests run inside Docker — never install Python deps locally:

```bash
# Build test image once (needs SSH agent for private dep)
DOCKER_BUILDKIT=1 docker build --ssh default -f Dockerfile.test -t aigent-test .

# Run tests (volume mount — no rebuild needed for code changes)
docker run --rm \
  -v "$(pwd)/src:/app/src" \
  -v "$(pwd)/tests:/app/tests" \
  aigent-test
```

Coverage gate: ≥90% enforced via `.coveragerc`.
