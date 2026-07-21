# Design: Helm Chart

## Decisão de arquitetura do chart

Os 7 serviços são quase idênticos (FastAPI, mesma imagem-base, diferindo em: nome, porta, IRSA role, env, scaling, dependências). Três abordagens:

| Abordagem | Prós | Contras | Veredito |
|-----------|------|---------|----------|
| **A. Single chart + loop `range` sobre `.Values.services`** | DRY total; um template por tipo de recurso; fácil adicionar serviço | templates com mais lógica (`range`/`include`) | ✅ **Escolhida** |
| B. Umbrella chart + 1 subchart por serviço | isolamento forte | 7× duplicação; manutenção pesada para serviços quase iguais | ❌ |
| C. Library chart + 7 charts finos | reuso via `define`/`include` | overhead de 8 charts para uma app pequena | ❌ (overkill agora) |

**Escolha: A.** Um chart `aigent-squad`, um mapa `services` em `values.yaml`, e templates que iteram com `range`. Cada recurso (Deployment/Rollout, Service, SA, ScaledObject, NetworkPolicy) é renderizado por serviço via helper `_helpers.tpl`. Se o projeto crescer muito, migra-se para C sem quebrar consumidores (mesmo `values`).

## Estrutura do chart

```
helm/aigent-squad/
├── Chart.yaml                  # version (chart) + appVersion (= versão da app)
├── values.yaml                 # defaults: mapa `services`, global, flags
├── values-dev.yaml             # overrides DEV (Deployment, réplicas baixas, OTLP dev)
├── values-hml.yaml
├── values-prd.yaml             # Rollout canary, réplicas altas, NetworkPolicy on
├── values-btc.yaml
├── templates/
│   ├── _helpers.tpl            # labels comuns, selectorLabels, nome, image
│   ├── serviceaccount.yaml     # range services → SA + IRSA annotation
│   ├── deployment.yaml         # range services WHERE rollout=false
│   ├── rollout.yaml            # range services WHERE rollout=true (Argo Rollouts)
│   ├── service.yaml            # range services → ClusterIP
│   ├── scaledobject.yaml       # range services → KEDA ScaledObject
│   ├── networkpolicy.yaml      # ingress: agentes←supervisor, supervisor←ingress/mcp
│   ├── externalsecret.yaml     # ExternalSecret → Secret `aigent-squad-secrets`
│   ├── configmap.yaml          # env não-sensível compartilhado
│   ├── ingress.yaml            # só supervisor + mcp-server
│   ├── rbac.yaml               # Role/RoleBinding read-only p/ kubernetes-agent
│   └── NOTES.txt
└── README.md                   # uso, values, exemplos helm template
```

## `values.yaml` (forma)

```yaml
global:
  image:
    repository: harbor.<org>.app.br/aigent-squad   # Kyverno reescreve p/ proxy
    tag: ""                                       # default = .Chart.AppVersion
  environment: DEV                                # DEV/HML/PRD/BTC
  costCenter: Platform-Infrastructure             # CONFIRMAR com tags.md
  otelEndpoint: ""                                # OTEL_EXPORTER_OTLP_ENDPOINT
  awsRegion: us-east-1
  bedrockModelId: ""                              # fonte única (= config.py default)

useRollout: false                                 # true em PRD/HML (canary)

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

# Mapa de serviços — fonte da verdade do range
services:
  supervisor:
    port: 8000
    public: true                # entra no Ingress
    iamRole: ""                 # supervisor não chama AWS direto
    extraEnv: [SLACK_*, DYNAMODB_*]
    scaling: { min: 2, max: 10, trigger: cpu, value: "70" }
  aws-agent:
    port: 8001
    iamRole: arn:aws:iam::<acct>:role/aigent-aws-agent   # IRSA
    scaling: { min: 1, max: 8, trigger: cpu, value: "70" }
  kubernetes-agent:
    port: 8002
    rbac: true                  # cria Role read-only
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
    command: mcp                # imagem/cmd do mcp-server
    scaling: { min: 1, max: 4 }

resources:
  requests: { cpu: 100m, memory: 256Mi }
  limits:   { memory: 512Mi }     # sem CPU limit (ScaleOps)

securityContext:
  runAsNonRoot: true
  runAsUser: 65534
  readOnlyRootFilesystem: true
  allowPrivilegeEscalation: false
  capabilities: { drop: ["ALL"] }
```

## `_helpers.tpl` — labels obrigatórios

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

## Deployment/Rollout (esqueleto, por serviço)

Comum aos dois (a diferença é `kind` e `strategy`):

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
            # REDIS_HOST/REDIS_PASSWORD/... idem
          livenessProbe:  { httpGet: { path: /healthz, port: http }, initialDelaySeconds: 5, periodSeconds: 10 }
          readinessProbe: { httpGet: { path: /ready,   port: http }, initialDelaySeconds: 5, periodSeconds: 5 }
          lifecycle: { preStop: { exec: { command: ["sh","-c","sleep 5"] } } }
          resources: {{ .Values.resources }}
          volumeMounts: [{ name: tmp, mountPath: /tmp }]
      terminationGracePeriodSeconds: 30
      volumes: [{ name: tmp, emptyDir: {} }]    # readOnlyRootFilesystem precisa disso
```

Rollout (PRD/HML) adiciona:
```yaml
strategy:
  canary:
    steps: [{ setWeight: 20 }, { pause: { duration: 60s } }, { setWeight: 50 }, { pause: { duration: 60s } }, { setWeight: 100 }]
```

## KEDA ScaledObject (por serviço)

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
> HPA cru é proibido (`k8s-best-practices`). KEDA é o superset.

## NetworkPolicy (zero-trust interno)

- **agentes (8001–8005)**: ingress só de pods com label `app.kubernetes.io/name=supervisor`.
- **supervisor (8000)**: ingress do ingress-controller e do `mcp-server`.
- **mcp-server (8006)**: ingress do ingress-controller.
- egress liberado para DNS, Bedrock/AWS (443), Redis, DynamoDB, OTLP collector.

## IRSA + RBAC

- ServiceAccount por serviço; annotation `eks.amazonaws.com/role-arn` só onde `iamRole` definido (aws, finops; supervisor não chama AWS direto — só roteia).
- `kubernetes-agent`: `Role` com `verbs: [get, list, watch]` (read-only, casa com a política read-only da app) + `RoleBinding` à sua SA. Cluster-wide via `ClusterRole` se precisar ver todos os namespaces (o agente faz `list_pod_for_all_namespaces`) — **decisão**: `ClusterRole` read-only restrito a pods/nodes/namespaces.

## Secrets (External Secrets Operator)

Um `ExternalSecret` materializa `aigent-squad-secrets` a partir do AWS Secrets Manager (`aigent-squad/<env>`). Pods consomem via `secretKeyRef`. **Nada** de secret em values/ConfigMap (`cloud-security.md`).

## Backing services (fora do chart)

| Serviço | Como o chart consome |
|---------|----------------------|
| DynamoDB | env `DYNAMODB_SESSIONS_TABLE`; IRSA dá acesso |
| ElastiCache Redis | `REDIS_HOST` (ConfigMap) + `REDIS_PASSWORD` (ExternalSecret) + `REDIS_SSL=true` |
| Bedrock | IRSA; `BEDROCK_MODEL_ID` via global |

Provisionados por Terraform (spec futura de infra), não pelo chart.

## GAP de código (pré-requisito)

O steering exige probes `/healthz` (liveness) e `/ready` (readiness). O código atual só expõe `/health`. **Antes** de aplicar o chart em cluster, adicionar os dois endpoints (vira task na spec de código / parte da 02 ou nova). O chart já assume `/healthz` + `/ready` para não nascer divergente.

## Invariantes

- Uma imagem por serviço, tag = versão (sem `latest`); multi-arch (amd64+arm64/Graviton).
- Mesma imagem em todos os ambientes; só `values-<env>` muda (12-factor V/X).
- PRD/HML/BTC só via ArgoCD (GitOps); DEV pode `helm upgrade` manual.
- Todo pod: labels obrigatórios + `resources.requests` + securityContext + probes (senão Kyverno rejeita).

## Verificação

```bash
helm lint helm/aigent-squad -f helm/aigent-squad/values-prd.yaml
helm template aigent-squad helm/aigent-squad -f helm/aigent-squad/values-dev.yaml | kubectl apply --dry-run=client -f -
# checar: 7 SAs, 7 Services, 7 ScaledObjects, NetworkPolicies, ExternalSecret, Ingress (2 hosts)
```

## Dependências externas (add-ons de cluster — não no chart)

KEDA, Argo Rollouts, External Secrets Operator, cert-manager, Istio Ambient, Kyverno, ALB controller. Referência ao steering; instalados via helmfile do cluster.

## Decisões em aberto (CONFIRMAR com o usuário)

1. `CostCenter` correto (steering `clarification-protocol`: não inventar). Placeholder `Platform-Infrastructure`.
2. Domínios de Ingress (`aigent.<org>.app.br`?).
3. Conta AWS / ARNs das roles IRSA.
4. Namespace alvo (`aigent-squad-<env>`?).
5. `kubernetes-agent` precisa mesmo de `ClusterRole` (all namespaces) ou escopo por namespace?
