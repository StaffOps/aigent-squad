# Tasks: RCA Investigation Workflow

> Pré-requisito: `17-multi-agent-collaboration` (fan-out), `19-config-driven-platform` (datasources/limites via config), `09-otel-instrumentation` (trace/timestamps).

## Fase 1 — RCA single-round (MVP do diferencial)

- [x] T1: Modelos `Evidence` e `RCAResult` em `src/core/investigation.py` — done 2026-06-14
- [x] T2: Timeline builder — ordena `Evidence` temporal, marca candidatos a causa (deploy/config/restart) (depends on: T1) — done 2026-06-14
- [x] T3: Correlator — regra "≥3 sinais independentes → alta; 2 → média; 1 → baixa"; contra-evidência rebaixa (depends on: T1) — done 2026-06-14
- [x] T4: Decisor trivial-vs-investigar (limiar explícito, sem Bedrock extra) — done 2026-06-14
- [x] T5: `investigation.py` no supervisor — orquestra: janela temporal + fan-out de evidência (reusa spec 17) (depends on: T1, T4) — done 2026-06-14
- [x] T6: RCA synthesizer — 1 Bedrock call (Sonnet) funde evidência → `RCAResult` + prevenção (depends on: T2, T3, T5) — done 2026-06-14
- [x] T7: Intent/flag `mode=investigate` no supervisor + contrato de saída `RCAResult` (depends on: T5, T6) — done 2026-06-14
- [x] T8: Teto de custo (nº agentes, nº queries de evidência) lido do config (spec 19) e aplicado antes das chamadas (depends on: T5) — done 2026-06-14 (via env var cost cap)
- [x] T9 (test-author DIFERENTE do autor): pytest ≥90% — coleta paralela, timeline, correlação (3/2/1 sinais), contra-evidência, fast-path trivial, teto de custo (depends on: T7, T8) — done 2026-06-14
- [x] T10: Review independente (`code-review`): read-only preservado (zero remediação), anti-falsa-confiança, custo (depends on: T9) — done 2026-06-14
- [ ] T11: Smoke via Docker — 1 sintoma trivial (fast-path) + 1 cross-domain (investigação completa) (depends on: T10) — deferred (manual smoke only)

## Ordem sugerida
T1→T2/T3; T4; T5→T6→T7; T8; T9→T10→T11.

## Fase 1.5 — Evidence-model correlator + real-RCA proof (added 2026-07-04)

> Gap found in the 2026-07-04 product review: `EVIDENCE-MODEL.md` (causal layers,
> independence test, 14 root-cause signatures — deliberated 2026-06-02 precisely to
> REPLACE the naive "≥3 signals" rule) was never implemented; T3 above shipped the
> naive correlator. The differentiator's best design is on paper only. Additionally,
> no real investigation has ever run end-to-end on a real incident.

- [ ] T12a: **Signal-coverage audit** (PR-02) — map all 33 EVIDENCE-MODEL signals
      (C1–C8, M1–M13, I1–I8, T1–T4, E1–E4) to what today's adapters can ACTUALLY
      collect. Known already: observability's only datasource is a static
      `query=up` (no PromQL-by-symptom, no Loki, no Tempo); devops has no
      deploy-history query (no ArgoCD). Output: coverage table → scopes the
      `37-evidence-adapters` candidate (BACKLOG B-01). Do BEFORE T12 — the
      correlator's value is bounded by collectable evidence
- [ ] T12: Implement the EVIDENCE-MODEL correlator in `src/core/investigation.py` —
      `Evidence` dataclass extended (causal_layer, fault_domain, timestamp_precision,
      derivation_source), `count_independent()` with DERIVATION_PAIRS,
      `validate_temporal_order()` with per-source tolerances, layer-based
      `score_confidence()` (Track A/B, contra-evidence blockers)
- [ ] T13: LLM confidence as ceiling + soft floor (synthesizer can lower with logged
      justification, never raise) — per EVIDENCE-MODEL §5/§8 (depends on: T12)
- [ ] T14: Measure the gain — re-run the spec-35 RCA scenario baseline (T9) after T12/T13;
      record before/after in `evals/results/` (depends on: T12, spec 35 T9)
- [ ] T15: **Real-RCA existence proof** — wire Alertmanager (or manually replay a real
      past incident's alert) in devops-core → `/alerts/incoming` → investigation →
      Slack post-back; write the result up as `docs/CASE-001.md` (symptom, evidence,
      RCA produced, human verdict on correctness). This is the product's first
      existence proof — worth more than any further platform work
- [ ] T16 (independent review): correlator vs EVIDENCE-MODEL spec — signatures, timing
      tolerances (CloudWatch 120s rule), independence semantics (depends on: T12–T14)

## Fase 2 — promotion triggers (parcialmente implementada)

Abrir Fase 2 **somente se** algum gatilho for observado em uso real:

| Gatilho | Capability da Fase 2 | Status |
|---------|----------------------|--------|
| Maioria das RCAs precisa de 2+ rodadas pra concluir | Loop investigativo iterativo (coleta dirigida pela 1ª hipótese) | NOT IMPLEMENTED |
| Hipótese única erra com frequência | Multi-hipótese + fault-tree (ranquear hipóteses concorrentes) | NOT IMPLEMENTED |
| Operadores reabrem o mesmo incidente | Integração com `21-incident-memory-learning` (recuperar similares) | ✅ done (spec 21) |
| Volume justifica detecção proativa | Webhook que abre investigação a partir de alerta (sem usuário) | ✅ done 2026-06-14 |

**Phase 2 capabilities IMPLEMENTED 2026-06-14:**
- ✅ Alert ingestion via `POST /alerts/incoming` (Alertmanager v2)
- ✅ Fingerprint dedup via Redis (TTL configurável `ALERT_DEDUP_TTL`)
- ✅ Slack post-back via `SLACK_WEBHOOK_URL` (opt-in)
- ✅ Metrics: `aigent.alerts.received`, `.deduplicated`, `.investigation_triggered`, `.postback`
- ✅ docs/ALERTING.md

**Phase 2 capabilities still NOT IMPLEMENTED:**
- Multi-round iterative investigation
- Multi-hypothesis fault-tree

## Notas
- Read-only é invariante: a investigação **propõe** prevenção, nunca executa.
- Não reintroduzir LangGraph (steering `project.md`) — orquestração é código próprio.
- Custo é o maior risco — teto via config é gate duro, não recomendação.
- Pipeline de verificação (`verification-independence.md`): T1–T8 autor; T9 test-author em sessão diferente; T10 code-review.

## Status (2026-06-14)

**Completed**: Phase 1 (T1–T10). Evidence/RCAResult models, timeline builder, correlator, triage decisor, investigation orchestrator, RCA synthesizer, `mode=investigate` flag, cost cap via env, tests, and code-review.

**NOT implemented (Phase 2)**:
- Multi-round iterative investigation (loop dirigido pela 1ª hipótese)
- Alert ingestion endpoint (`/alerts/incoming`)
- Multi-hypothesis fault-tree (ranquear hipóteses concorrentes)
- These remain as promotion triggers — implement only when real usage demonstrates Phase 1 is insufficient.

**Deferred**: T11 formal smoke test (manual smoke performed), Phase 2 entirely.
