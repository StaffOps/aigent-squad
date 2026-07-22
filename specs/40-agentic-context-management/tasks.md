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
gate ≥90% (see verification-independence + harness-engineering).

- [ ] T1 Config: `AIGENT_CONTEXT_TRIM_ENABLED` (true), `AIGENT_CONTEXT_KEEP_LAST_N` (3) in `agent_config.py`.
- [ ] T2 `_trim_message_history(messages, keep_last_n)` — summarize older `toolResult` content, preserve `tool_use`↔`toolResult` pairing, never touch current turn / user question.
- [ ] T3 Call before each Converse in `agentic_loop.py` + `agentic_loop_streaming.py`.
- [ ] T4 Independent tests: bounded growth, pairing preserved, current-turn intact, trim-off = no-op, keep-last-N boundary. ≥90% cov.
- [ ] T5 `code-review` — refute: lossy summary breaks RCA, pairing break, fan-out interaction.
- [ ] T6 Build + deploy + homologate: 12+ step query completes; compare `tokens_used`/`elapsed_ms` before vs after; eval 6/6 green.
- [ ] T7 Docs (CHANGES/BACKLOG/AGENTS/spec) + ROADMAP regen + gate rc=0.

## Status
Phase 1 pending — spec drafted, awaiting harness-review before implementation.
