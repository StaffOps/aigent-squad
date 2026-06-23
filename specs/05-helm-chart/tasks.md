# Tasks: Helm Chart

> Pré-requisito de código (GAP): adicionar endpoints `/healthz` e `/ready` aos serviços antes de aplicar em cluster. Rastreado aqui como T0.

- [x] T0: (código) Adicionar `/healthz` (liveness) e `/ready` (readiness) — concluído na spec 07 (2026-06-17)
- [x] T1: Esqueleto `charts/aigent-squad/` — `Chart.yaml`, `values.yaml` com mapa `services`, `global`, flags
- [x] T2: `_helpers.tpl` — labels obrigatórios, selectorLabels, nome, image ref
- [x] T3: `serviceaccount.yaml` — range services + annotation IRSA condicional
- [x] T4: `workload.yaml` — Deployment e StatefulSet; probes, securityContext, env, preStop, emptyDir
- [x] T6: `service.yaml` — ClusterIP por serviço (+ headless para StatefulSet)
- [x] T7: `autoscaling.yaml` — HPA (`autoscaling/v2`) e KEDA (`ScaledObject`) por serviço
- [x] T8: `externalsecret.yaml` — ExternalSecret → AWS Secrets Manager
- [x] T9: `configmap-agents.yaml` — ConfigMaps com agent.yaml + prompt.md por agente
- [x] T10: `rbac.yaml` — ClusterRole read-only + binding para kubernetes-agent
- [x] T11: `networkpolicy.yaml` — agentes←supervisor, supervisor←ingress/mcp
- [x] T12: `routing.yaml` — Ingress e Gateway API; só supervisor + mcp expostos
- [x] T13: `NOTES.txt` + `README.md`
- [ ] T15: `helm lint` + `helm template | kubectl apply --dry-run=client` (depends on: T1-T13)
- [ ] T16: (opcional) ArgoCD `Application`/ApplicationSet por env

## Ordem sugerida
T0 → T1 → T2 → (T3,T4,T6,T8,T9) → (T7,T10,T11,T12) → T13 → T15 → T16.

## Bloqueios — confirmar antes de T15/T16
- ARNs IRSA reais, namespace alvo, escopo RBAC do kubernetes-agent, host de Ingress.

## Notas
- Não embutir secrets em values (usar ExternalSecret).
- Não prefixar imagem com Harbor proxy (Kyverno mutará).
- Sem `latest`; tag = `appVersion`. Imagens multi-arch.
- Validar via Docker/helm CLI (sem aplicar em cluster real sem aprovação).
