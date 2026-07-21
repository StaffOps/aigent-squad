# Tasks: Observability & RCA uplift

Legenda: `[ ]` pendente · `[~]` parcial · `[x]` done. Código segue o harness
(`dev` implementa → `dev` testa independente → `code-review` → gate ≥90%).

## Phase 0 — Spec & round-table
- [ ] T0.1 Round-table (observability + security + sre + code-review) — refute WS1/WS2/WS3.
- [ ] T0.2 Verify the Grafana SA token backing grafana-mcp is **Viewer/read-only** (blocking WS1).

## WS1 — grafana-mcp wiring (quick win)
- [ ] T1.1 Enumerate grafana-mcp's read-only tools (tools/list) → define the allowlist (Loki query, Tempo search, Pyroscope, alerts/annotations, Incidents list, OnCall, Sift get).
- [ ] T1.2 Run `scripts/mcp_rbac_audit.py` on the grafana-mcp SA (done: PASS) + confirm Grafana token = Viewer.
- [ ] T1.3 Add the grafana-mcp datasource (read-only allowlist, streamable-http) to `agents/observability/agent.yaml` (GitLab live) — config-only.
- [ ] T1.4 Deploy (git-sync + supervisor reload) + homologate: a Loki logs query and a Tempo trace query return real data.

## WS2 — metric-catalog knowledge
- [ ] T2.1 Select the high-value metric catalogs (VM self, k8s-workload, dotnet/go/python/node APM, istio, kafka, argocd, karpenter) — canonical names.
- [ ] T2.2 Bring them into the squad `skill_registry` source as markdown skills with keyword tags.
- [ ] T2.3 List the relevant skills on `observability` (and `rca`) agents' `skills:`.
- [ ] T2.4 Homologate: a metric question yields a canonical name present in VM (verify via labels/`__name__`); add 2-3 golden queries to behavior baselines.

## WS3 — troubleshoot/RCA agent
- [ ] T3.1 Author `agents/rca/agent.yaml` (GitLab live): GenericAgent + vm-mcp + grafana-mcp + skills + routing_keywords (why, root cause, incident, down, failing, RCA) + `delegates_to` kubernetes/devops/aws.
- [ ] T3.2 RCA prompt: require ≥3 independent corroborating signals + calibrated confidence; leverage `investigation.py`.
- [ ] T3.3 Deploy + homologate on a seeded/real symptom → root cause cites ≥3 signals + confidence.
- [ ] T3.4 (Optional Phase 2) promote to a deterministic multi-query investigation path if the loop is insufficient.

## Docs & gate
- [ ] T4.1 Update BACKLOG/CHANGES/AGENTS/READ_ONLY/SECURITY + regen ROADMAP + `specs_status.py` green.
- [ ] T4.2 Mark spec 39 done when WS1–WS3 homologated.
