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
**Severity**: 🟢 Feature (Phase 2 — deploy)
**Related**: `ROADMAP.md` Phase 2; depends on `01-fix-blockers` and `04-harden-security`.

Design the Helm chart that packages AIgent-squad for EKS, aligned with StaffOps K8s steering (`k8s-best-practices.md`, `cloud-security.md`, `12-factor-app.md`, `observability-principles.md`). Today only `docker-compose.yaml` (local) exists; there are no K8s manifests.

## Services to package

| Service | Port | Scale type | Notes |
|---------|------|------------|-------|
| supervisor | 8000 | KEDA (load) | entry point; calls agents |
| aws-agent | 8001 | KEDA | boto3 (EC2/CE) — IRSA |
| kubernetes-agent | 8002 | KEDA | K8s API — RBAC read-only |
| finops-agent | 8003 | KEDA | CE + Athena — IRSA |
| devops-agent | 8004 | KEDA | GitLab + Docs portal |
| observability-agent | 8005 | KEDA | Prometheus HTTP |
| mcp-server | 8006 | KEDA | façade for Kiro CLI |

Backing services are **not** in the chart (they are managed): DynamoDB, ElastiCache Redis, Bedrock. The chart consumes them via env/secret.

## User Stories

WHEN the chart is installed in an environment (DEV/HML/PRD/BTC) THEN it SHALL create the 7 Deployments/Rollouts with environment-specific config via `values-<env>.yaml`.

WHEN a pod starts THEN it SHALL have the mandatory labels (`app.kubernetes.io/name`, `app.kubernetes.io/version`, `CostCenter`, `Environment`) and `resources.requests` defined.

WHEN an agent needs AWS access THEN it SHALL use a ServiceAccount annotated with IRSA (no mounted credentials).

WHEN the chart consumes secrets THEN they SHALL come from `ExternalSecret` (AWS Secrets Manager), never from values/ConfigMap.

WHEN the cluster does scheduling THEN each container SHALL expose liveness/readiness probes and run with restricted securityContext (non-root, readOnlyRootFilesystem, drop ALL).

WHEN traffic enters THEN only the supervisor and mcp-server SHALL be exposed via Ingress/Gateway; the 5 agents SHALL be internal `ClusterIP`, accessible only by the supervisor (NetworkPolicy).

WHEN the environment is PRD/HML/BTC THEN deployment SHALL occur via ArgoCD (GitOps), and the chart SHALL support Argo Rollouts (canary).

## Acceptance Criteria

- [ ] `helm template` and `helm lint` pass for all environments (`values-dev/hml/prd/btc`).
- [ ] A single chart parametrizes all 7 services (no template duplication per service).
- [ ] Mandatory labels + `resources.requests` on all pods (Kyverno-compliant).
- [ ] ServiceAccount per service with IRSA annotation (`eks.amazonaws.com/role-arn`).
- [ ] `ExternalSecret` for: internal token (`INTERNAL_API_TOKEN`), `REDIS_PASSWORD`, `GITLAB_TOKEN`, `SLACK_*`, `DOCS_PORTAL_TOKEN`.
- [ ] Probes `/healthz` (liveness) and `/ready` (readiness) — **requires code adjustment** (today only `/health` exists).
- [ ] Restricted `securityContext` + `emptyDir` for writable dirs (e.g.: kube cache).
- [ ] `KEDA ScaledObject` per service (not raw HPA); replica ranges per env.
- [ ] `NetworkPolicy`: agents only accept ingress from supervisor; supervisor only from ingress/mcp.
- [ ] Images via Harbor proxy (Kyverno will mutate; don't prefix manually); tag = version (no `latest`).
- [ ] `preStop` + `terminationGracePeriodSeconds` (graceful shutdown).
- [ ] `Rollout` (canary) optional via flag; default `Deployment` in DEV.
- [ ] OTel: `SERVICE_NAME` per service + `OTEL_EXPORTER_OTLP_ENDPOINT` per env.

## Out of scope

- Provisioning DynamoDB/ElastiCache/IRSA roles (that is Terraform — Phase 2, future spec).
- Implementing `/healthz` and `/ready` endpoints in code (becomes a task in the code spec; here we only consume).
- Installing ArgoCD/KEDA/External Secrets Operator (cluster add-ons, outside the app chart).
- Helm chart for the add-ons (this chart is only for the application).
