# Installation

## Local development

### 1. Clone

```bash
git clone git@github.com:StaffOps/staffops-aigent-squad.git
cd staffops-aigent-squad
```

### 2. Start the stack

```bash
make up      # builds + starts, waits for the gateway /ready check
make smoke   # health + 1 real query + /v1/models
```

No SSH key or private-repo access needed — `otel-helper` is a public
dependency (since 2026-07-14). A Bedrock-capable AWS credential IS needed
for `make smoke`'s real query (see [Prerequisites](prerequisites.md)); `make
lint` / `make test` alone need neither AWS nor a live Bedrock call.

| Service | URL | Purpose |
|---------|-----|---------|
| Gateway | http://localhost:8000 | Public front door (`/query`, `/v1/*`, health) |
| Supervisor | http://localhost:8001 | Backend-only (`/internal/*`) |
| MCP Server | http://localhost:8006 | Kiro CLI integration |
| Redis | localhost:6379 | Agent data cache |
| DynamoDB Local | localhost:8100 | Conversation history |

### 3. Verify

```bash
curl http://localhost:8000/healthz   # liveness — always 200
curl http://localhost:8000/ready     # readiness — checks supervisor + Redis + DynamoDB + agents
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

Everything runs via Docker + `make` — never install Python deps locally:

```bash
make test                          # full suite + 90% coverage gate
make test-one FILE=tests/test_x.py # single file
make lint                          # ruff, CI-verbatim scope
make typecheck                     # mypy gate (blocks the CI test job)
```

`make test` auto-stubs the `otel-helper` dependency locally
(`scripts/test-local.sh`) so the suite runs the same whether or not you have
network access to it — CI uses the real one. Coverage gate: ≥90%, enforced
in Docker.
