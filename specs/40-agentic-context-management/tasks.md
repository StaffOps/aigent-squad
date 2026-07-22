---
spec: 40-agentic-context-management
status: in-progress
completed: null
superseded_by: null
depends_on: ["37-agentic-tool-calling"]
deferred: []
---

# Tasks: Agentic-loop context management

Harness: `dev` implements → `dev` tests (independent session) → `code-review` →
gate ≥90%. **Spec revised per harness DC1–DC5 (see design.md).**

- [ ] T1 Config: `AIGENT_CONTEXT_TRIM_ENABLED` (true), `AIGENT_CONTEXT_KEEP_LAST_N` (**5**, floor `max(N,1)`) in `agent_config.py`.
- [ ] T2 `truncation.py`: `_summarize_for_context(tool_name, tool_args, result_text)` (DC1 format) + `trim_message_history(messages, keep_last_n)` — DC2 (replace only `toolResult.content.text`, keep `toolUseId`), DC3 (per-block in fan-out; discriminator; never the user question), DC4 (never last 2 messages).
- [ ] T3 Single callsite in `agentic_loop.py` + `agentic_loop_streaming.py` before each Converse (DC5 shared module).
- [ ] T4 Independent tests (9): N=0→1 floor; N=1; fan-out (3 toolResults) all summarized when old; single-step no-op; 8-step → 1-3 trimmed / 4-8 verbatim (N=5); user question never touched; **toolUseId pairing invariant**; mocked Converse accepts trimmed structure (no ValidationException). ≥90% cov.
- [ ] T5 `code-review` — confirm DC1–DC5 honored + refute residual R1/R2.
- [ ] T6 Build + deploy + homologate: 12+ step query completes; per-turn input **plateaus** after step N+1; compare `tokens_used`/`elapsed_ms` before vs after; eval 6/6 + a trimmed-step cite case.
- [ ] T7 Docs (CHANGES/BACKLOG/AGENTS/spec) + ROADMAP regen + gate rc=0.

## Status
Phase 1 — spec revised per harness (DC1–DC5). **GO for implementation.**
