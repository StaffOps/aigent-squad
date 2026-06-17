# Tasks: Helm Chart

> Pré-requisito de código (GAP): adicionar endpoints `/healthz` e `/ready` aos serviços antes de aplicar em cluster. Rastreado aqui como T0.

- [ ] T0: (código) Adicionar `/healthz` (liveness) e `/ready` (readiness) aos 7 serviços — pré-req do chart
- [ ] T1: Esqueleto `helm/aigent-squad/` — `Chart.yaml` (version + appVersion), `values.yaml` com mapa `services`, `global`, flags
- [ ] T2: `_helpers.tpl` — labels obrigatórios (`name/version/CostCenter/Environment/part-of`), selectorLabels, nome, image ref (depends on: T1)
- [ ] T3: `serviceaccount.yaml` — range services + annotation IRSA condicional (`iamRole`) (depends on: T2)
- [ ] T4: `deployment.yaml` — range services WHERE `useRollout=false`; probes, securityContext, env, secretKeyRef, preStop, emptyDir (depends on: T2)
- [ ] T5: `rollout.yaml` — range services WHERE `useRollout=true`; canary steps (depends on: T2)
- [ ] T6: `service.yaml` — ClusterIP por serviço (depends on: T2)
- [ ] T7: `scaledobject.yaml` — KEDA por serviço; scaleTargetRef Deployment/Rollout conforme flag (depends on: T4,T5)
- [ ] T8: `externalsecret.yaml` — materializa `aigent-squad-secrets` do AWS Secrets Manager (depends on: T1)
- [ ] T9: `configmap.yaml` — env não-sensível compartilhado (REDIS_HOST, DYNAMODB_*, etc) (depends on: T1)
- [ ] T10: `rbac.yaml` — ClusterRole read-only + binding para `kubernetes-agent` (depends on: T3)
- [ ] T11: `networkpolicy.yaml` — agentes←supervisor, supervisor←ingress/mcp, egress controlado (depends on: T6)
- [ ] T12: `ingress.yaml` — só supervisor + mcp-server (ALB), hosts por env (depends on: T6)
- [ ] T13: `NOTES.txt` + `helm/aigent-squad/README.md` (uso, values, exemplos)
- [ ] T14: `values-dev/hml/prd/btc.yaml` — overrides (Deployment vs Rollout, réplicas, OTLP, NetworkPolicy) (depends on: T1)
- [ ] T15: `helm lint` + `helm template | kubectl apply --dry-run=client` para os 4 envs (depends on: T1-T14)
- [ ] T16: (opcional) ArgoCD `Application`/ApplicationSet apontando para o chart por env

## Ordem sugerida
T0 (código) em paralelo. T1 → T2 → (T3,T4,T5,T6,T8,T9) → (T7,T10,T11,T12) → T13/T14 → T15 → T16.

## Bloqueios — confirmar antes de T1/T14 (ver design.md "Decisões em aberto")
- CostCenter correto, domínios de Ingress, conta AWS + ARNs IRSA, namespace alvo, escopo RBAC do k8s-agent.

## Notas
- Não embutir secrets em values (usar ExternalSecret).
- Não prefixar imagem com Harbor proxy (Kyverno mutará).
- Sem `latest`; tag = `appVersion`. Imagens multi-arch.
- Validar via Docker/helm CLI (sem aplicar em cluster real sem aprovação).
