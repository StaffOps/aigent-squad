---
spec: 40-agentic-context-management
status: done
completed: 2026-07-22
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

**Rationale (in order of strength)**:
1. Converse requires `tool_use` ↔ `toolResult` pairing — dropping a `toolResult`
   breaks the API contract. Summarizing content keeps the pairing valid.
2. The result CONTENT is the token driver, not the turn count.
3. Preserves the reasoning chain (model still sees it queried X → got <summary>).

**Accepted trade-offs**:
| Cost | Reality |
|-------|-----------|
| Older detail is lossy | The model already used it to choose next steps; a summary suffices for final synthesis. Recent N kept verbatim. |
| Summary quality varies | Deterministic (shape/first-line/count), NOT an LLM call — cheap + predictable, no extra latency/cost. |

**When this would be wrong (signals to reopen)**: answers start missing facts that
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

## Harness review outcomes (mandatory — supersede the above where they conflict)

The design round-table (code-review + observability) returned **NO-GO as written,
GO after DC1–DC5**. These are binding:

- **DC1 — new summary function (do NOT reuse `_summarize_tool_result`).** That
  function emits shape-only ("📦 12 items") → the model cannot cite values or
  correlate. Add `_summarize_for_context(tool_name, tool_args, result_text)` in
  `truncation.py`, deterministic (no LLM), output: `[context-trimmed] <tool> | args:<…200> | shape:<n items/chars> | sample:<first 3 lines/values> | keys:<top-5>`.
  Acceptance: model can answer "what did step 2 show?" with a specific sample value.
- **DC2 — replace ONLY `toolResult.content[i].text`.** Never touch the `toolResult`
  dict or `toolUseId`; no count-framing on summaries. `toolUseId` pairing is a
  tested invariant (else Bedrock `ValidationException`).
- **DC3 — per-block within fan-out turns.** Trim iterates INSIDE `msg["content"]`
  (a list of `toolResult` blocks). Counting unit = toolResult-bearing user messages.
  Discriminator: a user msg is a toolResult turn iff `any(b.get("toolResult") for b in content)`; the original question (has `text` blocks) is NEVER trimmed.
- **DC4 — N default = 5** (not 3; N=3 trims foundational steps 1-4 of an 8-step
  loop). Floor `effective_n = max(N, 1)`; never trim the last 2 messages
  (`assistant tool_use` + `user toolResult`).
- **DC5 — shared module `src/core/truncation.py`** (already hosts
  `truncate_tool_output`). Both loops call one `trim_message_history(...)`; no
  inline duplication.

**Residual risks (accepted, eval-gated):** R1 enriched summary still loses some
cross-step detail (mitigate: N=5 + eval case citing a trimmed-step value); R2
fan-out makes the bound worst-case N×tools-per-turn (document; Phase-2 byte-cap);
R4 add approx token counting (chars/4) + WARN when trimmed history >80% of
`MAX_LOOP_TOKENS`.

## Phase 1 (this spec)

Deterministic keep-last-N + shape summary. **NOT** an LLM-based summarizer.
Promotion trigger for Phase 2 (LLM summarizer): deterministic summaries prove too
lossy for RCA-style multi-signal queries in eval.
