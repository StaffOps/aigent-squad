# Design: Observability & RCA uplift

## Architecture

```
                    ┌─ vm-mcp (VictoriaMetrics: metrics/MetricsQL) ─ read-only
observability ──────┼─ grafana-mcp (Loki logs · Tempo traces · Pyroscope ──── read-only
   + rca agent      │   profiles · alerts/annotations · Incidents/OnCall · Sift)
                    └─ skills: per-component metric-name catalogs (<skills> block)

rca agent (GenericAgent, config-only) → agentic loop pulls the signals it needs;
  RCA prompt requires ≥3 corroborating signals before asserting a cause;
  delegates_to: kubernetes / devops / aws for domain drill-down.
```

## Components

| Component | Change |
|-----------|--------|
| `agents/observability/agent.yaml` (GitLab live) | add `grafana-mcp` datasource (read-only allowlist) + `skills: [<metric catalogs>]` |
| `agents/rca/agent.yaml` (NEW, GitLab live) | GenericAgent with vm-mcp + grafana-mcp + skills + RCA prompt + delegates_to |
| squad `skill_registry` source | add curated metric-catalog skills (markdown + keyword tags) |
| `scripts/mcp_rbac_audit.py` | run against `grafana-mcp` SA (already PASS: 23 read-only rules) |

## Rationale (decisões)

### Decision 1: Reuse the already-deployed grafana-mcp (no new infra)
**Choice**: wire the existing `grafana-mcp` (svc :8000, SA read-only) instead of
adding per-backend MCPs. **Rationale**: one server already exposes Loki/Tempo/
Pyroscope/alerts/Incidents/OnCall/Sift read-only; zero infra, one SA to audit.
**Trade-off**: grafana-mcp's read-only-ness for *Grafana operations* depends on the
**Grafana service-account token role** — it MUST be `Viewer` (read-only). The K8s
SA-RBAC gate covers K8s; the Grafana token scope is a separate, mandatory check.
**When this would be wrong**: if the Grafana token were Editor/Admin → it could mutate
Grafana. Mitigation: verify + pin the token to Viewer; document as an invariant.

### Decision 2: Metric catalogs as `skills` (reference), not prompt instructions
**Choice**: load per-component metric-name catalogs via `skill_registry`, rendered
inside `<skills>` and explicitly framed as *reference knowledge, not instructions*.
**Rationale**: kills metric-name hallucination (the model uses canonical names),
lazy-loaded by keyword so it doesn't bloat every prompt. **Trade-off**: catalogs
drift as the stack changes → tag with a source/date and re-audit periodically.
**Alternative considered and discarded**: indexing into the pgvector KB — heavier, and the skill
mechanism already fits (markdown + keyword select).

### Decision 3: RCA agent is config-only (GenericAgent), not new orchestration
**Choice**: `agents/rca/agent.yaml` reuses the GenericAgent agentic loop; the RCA
behavior comes from its datasource set + prompt (≥3-signal corroboration) +
`delegates_to`. **Rationale**: spec 37 already gives the loop; `investigation.py`
already exists for synthesis. Zero/low new code. **When this would be wrong**: if
cross-signal correlation needs deterministic multi-query orchestration the loop
can't express → then promote to a dedicated investigation path (Phase 2).

## Invariants
- 100% read-only (allowlist + per-MCP SA-RBAC gate + **Grafana token = Viewer**).
- Skills are reference knowledge, never instructions (prompt-injection safe).
- RCA asserts a cause only with ≥3 independent corroborating signals (evidence-before-assertion).
- Adding grafana-mcp / the RCA agent is **config-only** (spec 37 invariant) — zero code where possible.

## Failure modes
- Grafana token over-privileged → mutation risk. Mitigation: Viewer-only + audit.
- grafana-mcp tool result too large (log floods) → the spec-37 truncation cap + count-marker apply.
- RCA agent over-asserts on 1 signal → prompt + (optional) a groundedness gate reject.

---

## Round-table outcomes (blocking fixes — 2026-07-21)

The observability/security/sre/code-review round-table refuted the initial draft.
Blocking fixes folded in:

### Decision 4: RCA routing — additional specialist, NOT a replacement (config vs code)
`triage.py::should_investigate()` intercepts RCA-like queries **before** the classifier
and calls the dedicated `run_investigation()` path. So a plain `agents/rca/agent.yaml`
GenericAgent would be **dead code** for those queries. **Choice**: WS3 wires grafana-mcp
+ skills into the EXISTING investigation path (`investigation.py`) and adds `rca` as a
routable specialist for observability-flavored "why" questions that fall through triage —
this **requires code** in `triage.py`/`investigation.py`, so WS3 is **NOT config-only**
(the initial claim was wrong). `delegates_to` is **prompt-hint only** (unused at runtime) —
documented honestly; real fan-out delegation is a separate code change.

### Security mitigations (M-1 and M-10 are DEPLOY-BLOCKING)
- **M-1 (blocking): Grafana token = Viewer.** The K8s SA-RBAC gate does NOT cover the
  Grafana token role — that is the real write boundary. grafana-mcp must use a **Viewer**
  Grafana service-account token; add a **startup assertion** (query `/api/org` or `/api/user`
  → role must be `Viewer`, else refuse). Rotate via ESO.
- **M-10 (blocking): output secret/PII filter.** Logs/traces routinely leak Bearer/JWT/AKIA*/
  connection-strings/PII. Since the agent surfaces log content, add an **OUTPUT regex filter**
  (JWT, `Bearer `, `AKIA[0-9A-Z]{16}`, connection strings) + PII masking (email/IP/CPF/CNPJ)
  on top of the existing guardrail — the current PII filter has gaps here.
- **Indirect prompt-injection** via attacker-controlled log/trace content: hard `limit ≤ 50`
  lines per query, line truncation (~1024 chars), untrusted-data framing delimiters.
- **RCA scope**: default to the investigation namespace(s); **exclude sensitive namespaces**
  (kube-system, cert-manager, external-secrets, …) by default; audit every query.

### WS1 allowlist — EXACT tools, explicit mutating exclusions
- Allow (read-only): `query_loki_logs`, `query_loki_stats`, `list_loki_label_names/values`,
  `tempo_traceql-search`, `tempo_get-trace`, `tempo_get-attribute-*`, `query_pyroscope`,
  `list_pyroscope_*`, `list_incidents`, `get_incident`, `list_oncall_schedules`,
  `get_current_oncall_users`, `list_sift_investigations`, `get_sift_investigation`,
  `get_sift_analysis`, `search_dashboards`, `get_dashboard_by_uid`, `get_annotations`.
- **EXCLUDE (mutating)**: `create_annotation`, `update_annotation`, `add_activity_to_incident`,
  `create_incident`, `update_dashboard`, `create_datasource`, `update_datasource`,
  `install_plugin`, and `alerting_manage_rules` write ops.
- **Exclude `query_prometheus`** from grafana-mcp — vm-mcp is the canonical metrics path (dedup).
- **datasourceUid bootstrapping**: inject per-signal UIDs via `tool_arguments` (env vars), or
  add `list_datasources` to the allowlist (accept one extra round-trip). Without this the first
  Loki/Tempo/Pyroscope call fails "datasource not found".

### WS2 — source, selection, and a hard cap
- Source: the org's curated `apm-metrics` catalogs (canonical VM/Prometheus names, incl.
  semconv→Prometheus mapping). Priority: **P0** victoriametrics-self, k8s-workload,
  collector-internal · **P1** dotnet-apm, karpenter, argocd · **P2** istio, kafka, loki-tempo-self.
- **Invariant: MAX_SKILLS_PER_TURN = 3** (keyword-selected) — loading 10 catalogs blows the
  token budget and dilutes retrieval. Catalogs also need label guidance, not just metric names.

### WS3 — enforce grounding, anchor time, eval before trust
- **Mandatory groundedness gate** (not optional): after the loop, a separate validation pass
  must confirm the RCA lists raw signals + a causal mechanism + ≥1 disproved counter-hypothesis
  + falsifiability; else label "hypothesis, not RCA" and escalate.
- **Time-window anchoring**: the trigger (alert ts / user "since 14h") sets start/end — never
  guessed by the LLM.
- **Budgets**: per-query timeout ~10s; total triage budget ~3min; max delegation depth 2;
  gather-then-analyze (no ping-pong); "observability brownout" fallback to kubectl/events.
- **Phase 2 (deterministic investigation runner) is triggered, not optional** — promote when
  eval shows false-positive RCA > 20% (or <3-signal citations > 30%) in practice.
- **Eval harness before operators trust it**: golden incident replays (OOMKill-vs-deploy trap,
  Tempo OOM, NetworkPolicy block, HPA anti-affinity cap, CoreDNS, cold-cache) + a correctness
  scorecard (≥80% correct, <5% harmful) + shadow mode. Output labeled "AI-assisted hypothesis —
  requires human validation before action" until gates pass.

### Go / no-go
- **WS2 (metric skills)** — GO, safest, start here.
- **WS1 (grafana-mcp)** — GO **after** M-1 (token Viewer verified) + M-10 (output secret filter)
  + explicit allowlist. Not deployable without M-1/M-10.
- **WS3 (RCA)** — GO for a **Phase-1 hypothesis assistant only** (grounded, time-anchored,
  budgeted, clearly labeled), with the eval harness as the gate to operator trust; NOT config-only.
