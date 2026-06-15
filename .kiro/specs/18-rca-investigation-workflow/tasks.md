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

## Fase 2 — promotion triggers (NÃO implementar agora) — NOT IMPLEMENTED

Abrir Fase 2 **somente se** algum gatilho for observado em uso real:

| Gatilho | Capability da Fase 2 |
|---------|----------------------|
| Maioria das RCAs precisa de 2+ rodadas pra concluir | Loop investigativo iterativo (coleta dirigida pela 1ª hipótese) |
| Hipótese única erra com frequência | Multi-hipótese + fault-tree (ranquear hipóteses concorrentes) |
| Operadores reabrem o mesmo incidente | Integração com `21-incident-memory-learning` (recuperar similares) |
| Volume justifica detecção proativa | CronJob que abre investigação a partir de alerta (sem usuário) |

**Phase 2 capabilities NOT IMPLEMENTED:**
- Multi-round iterative investigation
- Alert ingestion via `/alerts/incoming` endpoint
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
