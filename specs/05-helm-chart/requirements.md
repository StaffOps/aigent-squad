---
spec: 05-helm-chart
status: superseded
completed: null
superseded_by: "22-agent-capability-manifest"
depends_on: []
deferred: []
---

# Feature: Helm Chart

**Spec**: `05-helm-chart`
**Severidade**: 🟢 Feature (Fase 2 — deploy)
**Relacionada**: `ROADMAP.md` Fase 2; depende de `01-fix-blockers` e `04-harden-security`.

Desenhar o Helm chart que empacota o AIgent-squad para EKS, alinhado ao steering K8s do StaffOps (`k8s-best-practices.md`, `cloud-security.md`, `12-factor-app.md`, `observability-principles.md`). Hoje só existe `docker-compose.yaml` (local); não há manifesto K8s.

## Serviços a empacotar

| Serviço | Porta | Tipo de escala | Notas |
|---------|-------|----------------|-------|
| supervisor | 8000 | KEDA (carga) | ponto de entrada; chama os agentes |
| aws-agent | 8001 | KEDA | boto3 (EC2/CE) — IRSA |
| kubernetes-agent | 8002 | KEDA | K8s API — RBAC read-only |
| finops-agent | 8003 | KEDA | CE + Athena — IRSA |
| devops-agent | 8004 | KEDA | GitLab + Docs portal |
| observability-agent | 8005 | KEDA | Prometheus HTTP |
| mcp-server | 8006 | KEDA | fachada para Kiro CLI |

Backing services **não** entram no chart (são gerenciados): DynamoDB, ElastiCache Redis, Bedrock. O chart os consome via env/secret.

## User Stories

WHEN o chart é instalado em um ambiente (DEV/HML/PRD/BTC) THEN ele SHALL criar os 7 Deployments/Rollouts com config específica do ambiente via `values-<env>.yaml`.

WHEN um pod sobe THEN ele SHALL ter os labels obrigatórios (`app.kubernetes.io/name`, `app.kubernetes.io/version`, `CostCenter`, `Environment`) e `resources.requests` definidos.

WHEN um agente precisa acessar AWS THEN ele SHALL usar uma ServiceAccount anotada com IRSA (sem credencial montada).

WHEN o chart consome secrets THEN eles SHALL vir de `ExternalSecret` (AWS Secrets Manager), nunca de values/ConfigMap.

WHEN o cluster faz scheduling THEN cada container SHALL expor liveness/readiness probes e rodar com securityContext restrito (non-root, readOnlyRootFilesystem, drop ALL).

WHEN o tráfego entra THEN só o supervisor e o mcp-server SHALL ser expostos via Ingress/Gateway; os 5 agentes SHALL ser `ClusterIP` internos, acessíveis apenas pelo supervisor (NetworkPolicy).

WHEN o ambiente é PRD/HML/BTC THEN o deploy SHALL ocorrer via ArgoCD (GitOps), e o chart SHALL suportar Argo Rollouts (canary).

## Acceptance Criteria

- [ ] `helm template` e `helm lint` passam para todos os ambientes (`values-dev/hml/prd/btc`).
- [ ] Um único chart parametriza os 7 serviços (sem duplicar template por serviço).
- [ ] Labels obrigatórios + `resources.requests` em todos os pods (Kyverno-compliant).
- [ ] ServiceAccount por serviço com annotation IRSA (`eks.amazonaws.com/role-arn`).
- [ ] `ExternalSecret` para: token interno (`INTERNAL_API_TOKEN`), `REDIS_PASSWORD`, `GITLAB_TOKEN`, `SLACK_*`, `DOCS_PORTAL_TOKEN`.
- [ ] Probes `/healthz` (liveness) e `/ready` (readiness) — **requer ajuste de código** (hoje só há `/health`).
- [ ] `securityContext` restrito + `emptyDir` para dirs graváveis (ex.: kube cache).
- [ ] `KEDA ScaledObject` por serviço (não HPA cru); ranges de réplicas por env.
- [ ] `NetworkPolicy`: agentes só aceitam ingress do supervisor; supervisor só do ingress/mcp.
- [ ] Imagens via Harbor proxy (Kyverno mutará; não prefixar manualmente); tag = versão (sem `latest`).
- [ ] `preStop` + `terminationGracePeriodSeconds` (graceful shutdown).
- [ ] `Rollout` (canary) opcional via flag; default `Deployment` em DEV.
- [ ] OTel: `SERVICE_NAME` por serviço + `OTEL_EXPORTER_OTLP_ENDPOINT` por env.

## Fora de escopo

- Provisionar DynamoDB/ElastiCache/IRSA roles (isso é Terraform — Fase 2, spec futura).
- Implementar os endpoints `/healthz` e `/ready` no código (vira task na spec de código; aqui só consumimos).
- Instalar ArgoCD/KEDA/External Secrets Operator (add-ons de cluster, fora do chart da app).
- Helm chart dos add-ons (este chart é só da aplicação).
