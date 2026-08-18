# Tasks: Helm Chart

> Code prerequisite (GAP): add `/healthz` and `/ready` endpoints to services before applying in cluster. Tracked here as T0.

- [x] T0: (code) Add `/healthz` (liveness) and `/ready` (readiness) — completed in spec 07 (2026-06-17)
- [x] T1: Skeleton `charts/aigent-squad/` — `Chart.yaml`, `values.yaml` with `services` map, `global`, flags
- [x] T2: `_helpers.tpl` — mandatory labels, selectorLabels, name, image ref
- [x] T3: `serviceaccount.yaml` — range services + conditional IRSA annotation
- [x] T4: `workload.yaml` — Deployment and StatefulSet; probes, securityContext, env, preStop, emptyDir
- [x] T6: `service.yaml` — ClusterIP per service (+ headless for StatefulSet)
- [x] T7: `autoscaling.yaml` — HPA (`autoscaling/v2`) and KEDA (`ScaledObject`) per service
- [x] T8: `externalsecret.yaml` — ExternalSecret → AWS Secrets Manager
- [x] T9: `configmap-agents.yaml` — ConfigMaps with agent.yaml + prompt.md per agent
- [x] T10: `rbac.yaml` — ClusterRole read-only + binding for kubernetes-agent
- [x] T11: `networkpolicy.yaml` — agents←supervisor, supervisor←ingress/mcp
- [x] T12: `routing.yaml` — Ingress and Gateway API; only supervisor + mcp exposed
- [x] T13: `NOTES.txt` + `README.md`
- [ ] T15: `helm lint` + `helm template | kubectl apply --dry-run=client` (depends on: T1-T13)
- [ ] T16: (optional) ArgoCD `Application`/ApplicationSet per env

## Suggested order
T0 → T1 → T2 → (T3,T4,T6,T8,T9) → (T7,T10,T11,T12) → T13 → T15 → T16.

## Blockers — confirm before T15/T16
- Real IRSA ARNs, target namespace, kubernetes-agent RBAC scope, Ingress host.

## Notes
- Do not embed secrets in values (use ExternalSecret).
- Do not prefix image with Harbor proxy (Kyverno will mutate).
- No `latest`; tag = `appVersion`. Multi-arch images.
- Validate via Docker/helm CLI (do not apply to a real cluster without approval).
