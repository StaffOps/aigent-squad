# Design: Helm Chart

## Chart architecture decision

The 7 services are nearly identical (FastAPI, same base image, differing in: name, port, IRSA role, env, scaling, dependencies). Three approaches:

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Single chart + `range` loop over `.Values.services`** | DRY total; one template per resource type; easy to add service | templates with more logic (`range`/`include`) | ✅ **Chosen** |
| B. Umbrella chart + 1 subchart per service | strong isolation | 7× duplication; heavy maintenance for nearly identical services | ❌ |
| C. Library chart + 7 thin charts | reuse via `define`/`include` | overhead of 8 charts for a small app | ❌ (overkill now) |

**Choice: A.** One `aigent-squad` chart, one `services` map in `values.yaml`, and templates that iterate with `range`. Each resource (Deployment/Rollout, Service, SA, ScaledObject, NetworkPolicy) is rendered per service via `_helpers.tpl` helpers. If the project grows substantially, migrate to C without breaking consumers (same `values`).

## Chart structure

```
helm/aigent-squad/
├── Chart.yaml                  # version (chart) + appVersion (= app version)
├── values.yaml                 # defaults: `services` map, global, flags
├── values-dev.yaml             # DEV overrides (Deployment, low replicas, dev OTLP)
├── values-hml.yaml
├── values-prd.yaml             # Rollout canary, high replicas, NetworkPolicy on
├── values-btc.yaml
├── templates/
│   ├── _helpers.tpl            # common labels, selectorLabels, name, image
│   ├── serviceaccount.yaml     # range services → SA + conditional IRSA annotation
│   ├── deployment.yaml         # range services WHERE rollout=false
│   ├── rollout.yaml            # range services WHERE rollout=true (Argo Rollouts)
│   ├── service.yaml            # range services → ClusterIP
│   ├── scaledobject.yaml       # range services → KEDA ScaledObject
│   ├── networkpolicy.yaml      # ingress: agents←supervisor, supervisor←ingress/mcp
│   ├── externalsecret.yaml     # ExternalSecret → Secret `aigent-squad-secrets`
│   ├── configmap.yaml          # shared non-sensitive env
│   ├── ingress.yaml            # only supervisor + mcp-server
│   ├── rbac.yaml               # Role/RoleBinding read-only for kubernetes-agent
│   └── NOTES.txt
└── README.md                   # usage, values, helm template examples
```

## `values.yaml` (shape)

```yaml
global:
  image:
    repository: harbor.<org>.app.br/aigent-squad   # Kyverno rewrites to proxy
    tag: ""                                       # default = .Chart.AppVersion
  environment: DEV                                # DEV/HML/PRD/BTC
  costCenter: Platform-Infrastructure             # CONFIRM with tags.md
  otelEndpoint: ""                                # OTEL_EXPORTER_OTLP_ENDPOINT
  awsRegion: us-east-1
  bedrockModelId: ""                              # single source (= config.py default)

useRollout: false                                 # true in PRD/HML (canary)

externalSecrets:
  enabled: true
  secretStoreRef: aws-secrets-manager
  remoteKey: aigent-squad/<env>                   # SecretsManager path
  keys: [INTERNAL_API_TOKEN, REDIS_PASSWORD, GITLAB_TOKEN,
         SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET, DOCS_PORTAL_TOKEN]

networkPolicy:
  enabled: true

ingress:
  enabled: true
  className: alb
  hosts:
    supervisor: aigent.<org>.app.br
    mcp: aigent-mcp.<org>.app.br

# Service map — source of truth for the range
services:
  supervisor:
    port: 8000
    public: true                # goes in the Ingress
    iamRole: ""                 # supervisor doesn't call AWS directly
    extraEnv: [SLACK_*, DYNAMODB_*]
    scaling: { min: 2, max: 10, trigger: cpu, value: "70" }
  aws-agent:
    port: 8001
    iamRole: arn:aws:iam::<acct>:role/aigent-aws-agent   # IRSA
    scaling: { min: 1, max: 8, trigger: cpu, value: "70" }
  kubernetes-agent:
    port: 8002
    rbac: true                  # creates read-only Role
    scaling: { min: 1, max: 6 }
  finops-agent:
    port: 8003
    iamRole: arn:aws:iam::<acct>:role/aigent-finops-agent
    extraEnv: [ATHENA_*]
    scaling: { min: 1, max: 3 }
  devops-agent:
    port: 8004
    extraEnv: [GITLAB_*, DOCS_PORTAL_*]
    scaling: { min: 1, max: 3 }
  observability-agent:
    port: 8005
    extraEnv: [PROMETHEUS_URL]
    scaling: { min: 1, max: 6 }
  mcp-server:
    port: 8006
    public: true
    command: mcp                # mcp-server image/cmd
    scaling: { min: 1, max: 4 }

resources:
  requests: { cpu: 100m, memory: 256Mi }
  limits:   { memory: 512Mi }     # no CPU limit (ScaleOps)

securityContext:
  runAsNonRoot: true
  runAsUser: 65534
  readOnlyRootFilesystem: true
  allowPrivilegeEscalation: false
  capabilities: { drop: ["ALL"] }
```

## `_helpers.tpl` — mandatory labels

```yaml
{{- define "aigent.labels" -}}
app.kubernetes.io/name: {{ .name }}
app.kubernetes.io/version: {{ .root.Chart.AppVersion | quote }}
app.kubernetes.io/part-of: aigent-squad
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
CostCenter: {{ .root.Values.global.costCenter }}
Environment: {{ .root.Values.global.environment }}
{{- end -}}
```

## Deployment/Rollout (skeleton, per service)

Common to both (the difference is `kind` and `strategy`):

```yaml
spec:
  template:
    spec:
      serviceAccountName: aigent-{{ $name }}
      securityContext: {{ .Values.securityContext }}
      containers:
        - name: {{ $name }}
          image: "{{ $.Values.global.image.repository }}-{{ $name }}:{{ $tag }}"
          ports: [{ name: http, containerPort: {{ $svc.port }} }]
          env:
            - { name: SERVICE_NAME, value: {{ $name }} }
            - { name: ENVIRONMENT, value: {{ $.Values.global.environment }} }
            - { name: OTEL_EXPORTER_OTLP_ENDPOINT, value: {{ $.Values.global.otelEndpoint }} }
            - { name: AWS_REGION, value: {{ $.Values.global.awsRegion }} }
            - { name: BEDROCK_MODEL_ID, value: {{ $.Values.global.bedrockModelId }} }
            - { name: INTERNAL_API_TOKEN, valueFrom: { secretKeyRef: { name: aigent-squad-secrets, key: INTERNAL_API_TOKEN } } }
            # REDIS_HOST/REDIS_PASSWORD/... same pattern
          livenessProbe:  { httpGet: { path: /healthz, port: http }, initialDelaySeconds: 5, periodSeconds: 10 }
          readinessProbe: { httpGet: { path: /ready,   port: http }, initialDelaySeconds: 5, periodSeconds: 5 }
          lifecycle: { preStop: { exec: { command: ["sh","-c","sleep 5"] } } }
          resources: {{ .Values.resources }}
          volumeMounts: [{ name: tmp, mountPath: /tmp }]
      terminationGracePeriodSeconds: 30
      volumes: [{ name: tmp, emptyDir: {} }]    # readOnlyRootFilesystem needs this
```

Rollout (PRD/HML) adds:
```yaml
strategy:
  canary:
    steps: [{ setWeight: 20 }, { pause: { duration: 60s } }, { setWeight: 50 }, { pause: { duration: 60s } }, { setWeight: 100 }]
```

## KEDA ScaledObject (per service)

```yaml
spec:
  scaleTargetRef: { name: aigent-{{ $name }}, kind: {{ if $.Values.useRollout }}Rollout{{ else }}Deployment{{ end }} }
  minReplicaCount: {{ $svc.scaling.min }}
  maxReplicaCount: {{ $svc.scaling.max }}
  triggers:
    - type: cpu
      metricType: Utilization
      metadata: { value: "{{ $svc.scaling.value | default "70" }}" }
```
> Raw HPA is forbidden (`k8s-best-practices`). KEDA is the superset.

## NetworkPolicy (internal zero-trust)

- **agents (8001–8005)**: ingress only from pods with label `app.kubernetes.io/name=supervisor`.
- **supervisor (8000)**: ingress from ingress-controller and `mcp-server`.
- **mcp-server (8006)**: ingress from ingress-controller.
- egress open for DNS, Bedrock/AWS (443), Redis, DynamoDB, OTLP collector.

## IRSA + RBAC

- ServiceAccount per service; annotation `eks.amazonaws.com/role-arn` only where `iamRole` is defined (aws, finops; supervisor doesn't call AWS directly — only routes).
- `kubernetes-agent`: `Role` with `verbs: [get, list, watch]` (read-only, matches the app's read-only policy) + `RoleBinding` to its SA. Cluster-wide via `ClusterRole` if it needs to see all namespaces (the agent does `list_pod_for_all_namespaces`) — **decision**: read-only `ClusterRole` restricted to pods/nodes/namespaces.

## Secrets (External Secrets Operator)

One `ExternalSecret` materializes `aigent-squad-secrets` from AWS Secrets Manager (`aigent-squad/<env>`). Pods consume via `secretKeyRef`. **Nothing** in values/ConfigMap (`cloud-security.md`).

## Backing services (outside the chart)

| Service | How the chart consumes |
|---------|------------------------|
| DynamoDB | env `DYNAMODB_SESSIONS_TABLE`; IRSA gives access |
| ElastiCache Redis | `REDIS_HOST` (ConfigMap) + `REDIS_PASSWORD` (ExternalSecret) + `REDIS_SSL=true` |
| Bedrock | IRSA; `BEDROCK_MODEL_ID` via global |

Provisioned by Terraform (future infra spec), not by the chart.

## Code GAP (prerequisite)

The steering requires `/healthz` (liveness) and `/ready` (readiness) probes. The current code only exposes `/health`. **Before** applying the chart to a cluster, add both endpoints (becomes a task in the code spec / part of 02 or new). The chart already assumes `/healthz` + `/ready` so it doesn't start divergent.

## Invariants

- One image per service, tag = version (no `latest`); multi-arch (amd64+arm64/Graviton).
- Same image in all environments; only `values-<env>` changes (12-factor V/X).
- PRD/HML/BTC only via ArgoCD (GitOps); DEV allows manual `helm upgrade`.
- Every pod: mandatory labels + `resources.requests` + securityContext + probes (otherwise Kyverno rejects).

## Verification

```bash
helm lint helm/aigent-squad -f helm/aigent-squad/values-prd.yaml
helm template aigent-squad helm/aigent-squad -f helm/aigent-squad/values-dev.yaml | kubectl apply --dry-run=client -f -
# check: 7 SAs, 7 Services, 7 ScaledObjects, NetworkPolicies, ExternalSecret, Ingress (2 hosts)
```

## External dependencies (cluster add-ons — not in the chart)

KEDA, Argo Rollouts, External Secrets Operator, cert-manager, Istio Ambient, Kyverno, ALB controller. Reference to steering; installed via cluster helmfile.

## Open decisions (CONFIRM with user)

1. Correct `CostCenter` (steering `clarification-protocol`: don't invent). Placeholder `Platform-Infrastructure`.
2. Ingress domains (`aigent.<org>.app.br`?).
3. AWS account / IRSA role ARNs.
4. Target namespace (`aigent-squad-<env>`?).
5. Does `kubernetes-agent` really need `ClusterRole` (all namespaces) or can it be scoped per namespace?
