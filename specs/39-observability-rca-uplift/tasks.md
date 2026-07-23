# Tasks: Observability & RCA uplift

Legenda: `[ ]` pendente · `[~]` parcial · `[x]` done. Código segue o harness
(`dev` implementa → `dev` testa independente → `code-review` → gate ≥90%).

> **Status 2026-07-21 (in-progress):** WS2 (metric-catalog skills) **DONE + live** (110 skills,
> canonical names, eval-gated). Round-table done. Shipped + validated (6/6 eval on agentic18): FU-1
> (metric-query discipline), B-16 (calibrated honesty), eval harness, loop-budget (steps 5→8, 30s→60s)
> + gateway-timeout tuning. **WS1 BLOCKED on M-1** — the Grafana SA token is write-capable (proven),
> must be reprovisioned as Viewer (terraform/admin). WS3 (RCA Phase-1) pending.
>
> **Update 2026-07-22 (post-homolog UX/robustness, live agentic20):** streaming tool-trace switched
> to `<think>` (LibreChat-collapsible; raw `<details>` showed as plain text) — configurable via
> `AIGENT_TRACE_STYLE`; `session_token_budget` raised 200K→2M (agentic queries cost ~30-60K each,
> old cap blocked chats after ~5 queries), env `SESSION_TOKEN_BUDGET`. Open "tempo" levers: vmselect
> OOM on devops-core (slow queries) + agent decisiveness on open-ended asks.

## Phase 0 — Spec & round-table
- [ ] T0.1 Round-table (observability + security + sre + code-review) — refute WS1/WS2/WS3.
- [ ] T0.2 Verify the Grafana SA token backing grafana-mcp is **Viewer/read-only** (blocking WS1).

## WS1 — grafana-mcp wiring (quick win) — DONE 2026-07-22
- [x] T1.1 Enumerated grafana-mcp read-only tools → allowlist (44 read: Loki query/stats/patterns, Tempo search/trace/attrs, Pyroscope, alerts/incidents/OnCall/Sift).
- [x] T1.2 `mcp_rbac_audit.py` on grafana-mcp SA = PASS (read-only). (Grafana **token** still write-capable → Viewer hardening DECLINED by user 2026-07-23; allowlist is the boundary.)
- [x] T1.3 grafana-mcp datasource added to `agents/observability/agent.yaml` (read-only allowlist, streamable-http).
- [x] T1.4 Homologated: Loki logs query drove 8+ tool calls with guardrail-redacted results.

## WS2 — metric-catalog knowledge
- [ ] T2.1 Select the high-value metric catalogs (VM self, k8s-workload, dotnet/go/python/node APM, istio, kafka, argocd, karpenter) — canonical names.
- [ ] T2.2 Bring them into the squad `skill_registry` source as markdown skills with keyword tags.
- [ ] T2.3 List the relevant skills on `observability` (and `rca`) agents' `skills:`.
- [ ] T2.4 Homologate: a metric question yields a canonical name present in VM (verify via labels/`__name__`); add 2-3 golden queries to behavior baselines.

## WS3 — cross-signal RCA (Phase-1: FOLDED into observability — harness verdict 2026-07-23)

Round-table (observability + sre + code-review + synth) **refuted a dedicated `rca` agent for
Phase-1**: same tools as observability (vm-mcp + grafana-mcp), so it adds routing ambiguity
("why is X slow?" matches both) + duplicate allowlist maintenance for **zero runtime
differentiation** (investigation.py wiring is Phase-2). Verdict: **fold RCA into observability**.

- [x] T3.1 (folded) RCA capability lives in the reinforced `observability` agent — cross-signal
  section (metric→trace→log→profile→alerts) + **Investigation Mode** (activate on why/root cause/
  incident/failing/outage/degraded).
- [x] T3.2 Prompt requires **≥3 independent corroborating signals** + timeline + refute-first +
  calibrated confidence + structured RCA output.
- [x] T3.3 RCA routing keywords added to `observability/agent.yaml`
  (root cause, rca, incident, why, failing, outage, degraded, investigation) + `delegates_to`
  kubernetes/devops/**aws**. (Live after push; homologate a seeded symptom → ≥3-signal RCA.)
- [ ] T3.4 (**Phase 2**) deterministic multi-query investigation path wiring `investigation.py`
  (Evidence/RCAResult/InvestigationState/build_timeline/correlate) into the agentic loop.
  **Extraction trigger** — split a dedicated `agents/rca/` ONLY when ALL hold: (a) investigation.py
  is wired (unique runtime behavior), (b) rca needs tools observability lacks, (c) the observability
  prompt bloats past ~3-4k tokens or routing accuracy drops. Until then, one agent = one toolConfig
  = no divergence.

## Docs & gate
- [ ] T4.1 Update BACKLOG/CHANGES/AGENTS/READ_ONLY/SECURITY + regen ROADMAP + `specs_status.py` green.
- [ ] T4.2 Mark spec 39 done when WS1–WS3 homologated.
