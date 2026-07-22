---
spec: 40-agentic-context-management
status: in-progress
completed: null
superseded_by: null
depends_on: ["37-agentic-tool-calling"]
deferred: []
---

# Design: Agentic-loop context management

## Where

`src/core/agentic_loop.py` + `src/core/agentic_loop_streaming.py`. Both build a
`messages` list and append, per tool-use turn: an assistant `tool_use` block, then
a user block of `toolResult`s. Trimming runs on this list **before each Converse
call**. A deterministic `_summarize_tool_result(tool_name, result)` already exists
(streaming loop ~line 161) and is reused.

## Strategy — keep-last-N verbatim + summarize older

- Keep the last **N** tool-result turns verbatim (N default 3).
- For turns older than N, replace each large `toolResult` content with a compact
  summary: `[trimmed] <tool_name> → <shape / first line / count> (full result in an
  earlier turn)`.
- Never trim the current turn's results, the assistant `tool_use` blocks (small),
  or the original user question.

## Rationale

### Decision 1: summarize older tool RESULTS in place (not drop turns)

**Choice**: rewrite older `toolResult` *content* to a summary; keep the turn/pairing.

**Justificativa (ordem de força)**:
1. Converse requires `tool_use` ↔ `toolResult` pairing — dropping a `toolResult`
   breaks the API contract. Summarizing content keeps the pairing valid.
2. The result CONTENT is the token driver, not the turn count.
3. Preserves the reasoning chain (model still sees it queried X → got <summary>).

**Trade-offs aceitos**:
| Custo | Realidade |
|-------|-----------|
| Older detail is lossy | The model already used it to choose next steps; a summary suffices for final synthesis. Recent N kept verbatim. |
| Summary quality varies | Deterministic (shape/first-line/count), NOT an LLM call — cheap + predictable, no extra latency/cost. |

**Quando estaria errado (signals para reabrir)**: answers start missing facts that
were in trimmed results (esp. RCA correlations) → raise N, enrich the summary, or
promote to an LLM-based summarizer (Phase 2).

## Invariants

- `tool_use` ↔ `toolResult` pairing ALWAYS preserved (Converse contract).
- Current-turn results never trimmed.
- The user's original question never dropped.
- Calibrated-honesty + B3 tool-result guardrails still apply to kept results.

## Config

| Env | Default | Meaning |
|-----|---------|---------|
| `AIGENT_CONTEXT_TRIM_ENABLED` | `true` | Master switch |
| `AIGENT_CONTEXT_KEEP_LAST_N` | `3` | Verbatim tool-result turns |

## Phase 1 (this spec)

Deterministic keep-last-N + shape summary. **NOT** an LLM-based summarizer.
Promotion trigger for Phase 2 (LLM summarizer): deterministic summaries prove too
lossy for RCA-style multi-signal queries in eval.
