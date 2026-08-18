---
spec: 40-agentic-context-management
status: done
completed: 2026-07-22
superseded_by: null
depends_on: ["37-agentic-tool-calling"]
deferred: []
---

# Feature: Agentic-loop context management (tool-result trimming)

Bound the per-turn context of the agentic loop so token cost and latency stay
flat as the number of tool steps grows — ending the "raise-the-limit" whack-a-mole.

## Problem

The agentic loop re-sends the FULL message history — including every prior tool
result — to Bedrock Converse on each turn. Tool results are large (up to
`MAX_TOOL_RESULT_CHARS` ≈ 10K tokens each), so per-turn input grows monotonically
with the number of steps.

Live evidence (2026-07-22, observability agent, one query):
- input tokens per turn: 15K → 24K → 39K → 44K … **cumulative 166K at step 5/8**.
- Consequence 1: hit `MAX_LOOP_TOKENS` (150K) mid-investigation → graceful
  budget-exhaustion note instead of an answer.
- Consequence 2: rising latency — each Converse turn re-processes a larger context
  (contributes to the 88–137s deep-query times).

Raising the limits (duration→120s, tokens→300K, read_timeout→120s) delayed the
failure but did not remove it: a deep enough query still exhausts and latency
stays high. Root cause = unbounded context growth.

## User stories

WHEN the loop accumulates more than the last N tool-result turns THEN the system
SHALL replace the OLDER tool results with a compact summary (tool name + shape/key
facts), keeping the last N turns verbatim, so per-turn input stays bounded.

WHEN a query needs many steps (12+) THEN the loop SHALL complete without hitting
`MAX_LOOP_TOKENS`, because per-turn context is bounded.

WHEN older results are summarized THEN the final answer SHALL preserve accuracy —
recent detail verbatim + a summary of earlier findings; no fabricated values
(calibrated-honesty still holds).

WHEN the operator tunes behavior THEN keep-last-N and enable/disable SHALL be
env-configurable (no rebuild).

## Acceptance criteria

- [ ] Per-turn input tokens stop growing monotonically — bounded after N turns.
- [ ] A 12+ step deep query completes without `MAX_LOOP_TOKENS` exhaustion.
- [ ] Golden-query eval stays green (no accuracy regression).
- [ ] Applied in BOTH `agentic_loop` and `agentic_loop_streaming`.
- [ ] Env-configurable (keep-last-N, on/off); default on.
- [ ] Deep-query latency measurably lower (fewer re-processed tokens).
- [ ] ≥90% coverage; independent tests; code-review.

## Out of scope

- Decisiveness prompt (separate lever — reduces call count).
- Model-tiering (spec 38).
- Cross-request/session memory (this is within a single loop).
