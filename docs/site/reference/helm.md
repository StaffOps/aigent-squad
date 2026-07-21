# Helm chart reference

The `staffops/aigent-squad` chart deploys the full AIgent-squad stack on Kubernetes.
It supports two topologies controlled by a single `topology` value and targets EKS
with AWS Bedrock, ElastiCache, and DynamoDB as backing services.

**Chart version**: `0.9.3`  
**Repository**: `https://staffops.github.io/helm-charts/`  
**Image**: `karlipegomes/aigent-squad:latest` (Docker Hub, multi-arch `amd64` + `arm64`)

---

## Add the repository

```bash
helm repo add staffops https://staffops.github.io/helm-charts/
helm repo update
```

---

## Topologies

### inProcess (default)

All five specialist agents run inside the supervisor process. A single Deployment
and Service are created. This is the recommended starting point and the only mode
validated for production in Phase 0.

```bash
helm install aigent-squad staffops/aigent-squad \
  --namespace aigent-squad --create-namespace \
  --set redis.host=my-elasticache.cache.amazonaws.com
```

### distributed

Each component (supervisor, five agents, MCP server) gets its own Deployment,
Service, and ServiceAccount. Use this topology when you need independent scaling
or want per-agent resource limits.

```bash
helm install aigent-squad staffops/aigent-squad \
  --namespace aigent-squad --create-namespace \
  -f https://raw.githubusercontent.com/StaffOps/helm-charts/main/charts/aigent-squad/values-distributed.yaml \
  --set redis.host=my-elasticache.cache.amazonaws.com
```

---

## Values reference

### Core

| Key | Default | Description |
|-----|---------|-------------|
| `topology` | `inProcess` | Deployment topology: `inProcess` or `distributed` |
| `global.image.registry` | `""` | Registry prefix; empty = Docker Hub direct |
| `services.supervisor.image.repository` | `karlipegomes/aigent-squad` | Image repository |
| `services.supervisor.image.tag` | `latest` | Image tag — use `sha-<commit>` to pin in production |

### LLM / Bedrock

| Key | Default | Description |
|-----|---------|-------------|
| `global.env.BEDROCK_MODEL_ID` | `us.anthropic.claude-sonnet-4-5-...` | Bedrock inference profile ID |

!!! warning "Single source of truth for the model ID"
    The model ID must also match what is set in `src/core/config.py` via the
    `BEDROCK_MODEL_ID` environment variable. The chart sets this variable for all
    containers through `global.env`.

### Probes

| Key | Default | Description |
|-----|---------|-------------|
| `global.probes.liveness.path` | `/healthz` | Liveness probe path |
| `global.probes.readiness.path` | `/ready` | Readiness probe path |

### Observability (chart 0.9.3+)

| Key | Default | Description |
|-----|---------|-------------|
| `global.otel.metricsPrometheusScrape` | `true` | Also expose `/metrics` on each service's own port for direct Prometheus/VictoriaMetrics scrape, in addition to the existing OTLP push through the collector (`otel-helper` v0.2.0+ runs both exporters on the same MeterProvider). |
| `serviceMonitor.enabled` | `false` | Create a Prometheus Operator `ServiceMonitor` per enabled service. Requires the CRD to already exist on the cluster — off by default since that isn't guaranteed. |

### Redis

| Key | Default | Description |
|-----|---------|-------------|
| `redis.host` | `""` | ElastiCache endpoint — **required in production** |
| `redis.inCluster.enabled` | `false` | Deploy an in-cluster Redis instance (development only) |

!!! warning "In-cluster Redis is not for production"
    `redis.inCluster.enabled: true` deploys a minimal Redis without persistence
    or HA. Use it only in local or CI environments.

### DynamoDB

| Key | Default | Description |
|-----|---------|-------------|
| `dynamodb.sessionsTable` | `agent-sessions` | DynamoDB table for conversation history |

### Routing

| Key | Default | Description |
|-----|---------|-------------|
| `routing.type` | `none` | External routing: `none`, `ingress`, or `gatewayapi` |

### Network policy

| Key | Default | Description |
|-----|---------|-------------|
| `networkPolicy.enabled` | `false` | Enable NetworkPolicy resource for pod-level isolation |

### Secrets

| Key | Default | Description |
|-----|---------|-------------|
| `externalSecrets.enabled` | `false` | Use External Secrets Operator to pull secrets from AWS Secrets Manager |

!!! info "Secrets management"
    Never place tokens or credentials in `values.yaml`. When `externalSecrets.enabled`
    is `true`, the chart creates `ExternalSecret` resources that pull values from AWS
    Secrets Manager. In environments without ESO, inject secrets via a CI/CD secret
    store or Kubernetes `Secret` objects managed outside the chart.

### Optional chat UI (chart 0.9.4+)

| Key | Default | Description |
|-----|---------|-------------|
| `librechat.enabled` | `false` | Deploy [LibreChat](https://github.com/danny-avila/LibreChat) + an in-cluster MongoDB `StatefulSet`, pre-wired to this release's gateway via the OpenAI-compatible bridge (spec 29) |
| `librechat.baseURL` | auto | Gateway URL LibreChat talks to; auto-computed to this release's own gateway Service when left empty |
| `librechat.apiKey` / `apiKeySecretName` | `""` | Token LibreChat sends as `X-Internal-Token`. If both are empty and `externalSecrets.enabled` is `true`, the chart injects `AIGENT_SQUAD_API_KEY` from the gateway's `INTERNAL_API_TOKEN` automatically |
| `librechat.allowRegistration` | `false` | Self-service signup. Off by default (internal tool → no default user). Enable temporarily to create the first account, then turn back off |
| `librechat.route.enabled` / `host` | `false` / `""` | Expose the UI via an Istio GatewayAPI HTTPRoute at `host`. Off → ClusterIP only (`port-forward svc/<release>-librechat 3080`) |
| `librechat.route.parentRef` / `annotations` / `labels` / `path` / `pathType` | `{}` / `PathPrefix` / `/` | Route customization; `parentRef` empty inherits `routing.gatewayapi.parentRef`. Set the `external-dns` hostname in `route.annotations` (same convention as the gateway route) |

!!! info "Minimal by design, not production-grade"
    This is a quick homologation/demo aid, not a hardened deployment: single
    Mongo pod, no HA, no auth on Mongo (same posture as `redis.inCluster`).
    JWT/CREDS secrets auto-generate per install unless pinned via
    `librechat.jwtSecret` etc. For anything beyond quick demo use, run
    LibreChat separately with its own production-grade Mongo and secret
    management.

### Agent configuration

| Key | Default | Description |
|-----|---------|-------------|
| `agentsSource.type` | `configmap` | Agent definition source: `configmap` (inline YAML) or `git` (clone at startup) |

---

## Security

Each service in the `distributed` topology receives its own `ServiceAccount` with
an IRSA annotation so Bedrock, DynamoDB, and ElastiCache access follows
least-privilege IAM roles at the pod level. In the `inProcess` topology, the
supervisor's `ServiceAccount` covers all agents.

Secrets are never stored in chart values. All sensitive material flows through
`ExternalSecret` (when `externalSecrets.enabled: true`) or a separately managed
Kubernetes `Secret`.

---

## Validation

Run the following before applying to a cluster:

```bash
# Lint
helm lint charts/aigent-squad

# Render default (inProcess) and inspect the output
helm template r charts/aigent-squad

# Render distributed and inspect
helm template r charts/aigent-squad \
  -f charts/aigent-squad/values-distributed.yaml
```

---

## Upgrade

```bash
helm upgrade aigent-squad staffops/aigent-squad \
  --namespace aigent-squad \
  --reuse-values \
  --set services.supervisor.image.tag=sha-<new-commit>
```

Always pin the image tag with a commit SHA in production — `latest` is
convenient for development but not reproducible.
