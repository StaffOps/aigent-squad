# Long-term Vision: Autonomous Multi-Agent System

> Moved out of `ROADMAP.md` by spec 32 (`32-spec-lifecycle-ssot`) T8, 2026-07-17 —
> ROADMAP is now plan-only. This is the north-star / phased-maturity document; it
> carries **no per-spec status** (that lives in each spec's frontmatter and the
> canonical table in `ROADMAP.md`).

**North star**: an autonomous agent system with shared memory and multi-step reasoning.
**Approach**: incremental evolution — each level is only justified when the
previous one proves a measurable limitation.

## Level 1 — Hub-and-spoke with fan-out (current specs)

The supervisor orchestrates; agents collect evidence independently; the
synthesizer correlates.

- **Delivers**: RCA in ~5s with cross-evidence from N agents in parallel.
- **Expected limitation**: blind collection — each agent doesn't know what the
  others found.
- **Promotion trigger to Level 2**: 1-round RCA is insufficient in >30% of cases
  (incomplete evidence, uncovered gaps).

## Level 2 — Iterative investigation

The synthesizer detects evidence gaps → triggers a targeted 2nd round (specific
agents, refined questions).

- **Delivers**: adaptive investigation that digs deeper where the 1st round was weak.
- **Expected limitation**: agents still operate in isolation — they refine
  without knowing what others found.
- **Promotion trigger to Level 3**: context from other agents would improve
  collection in >20% of cases (e.g. observability knowing devops found a recent
  deploy would change the metrics query).

## Level 3 — Shared memory + cross context

Agents receive a summary of what others collected (shared blackboard/scratchpad).
Each agent can refine its collection based on others' findings. It's not P2P
chat — it's 1 context broadcast → informed collection.

- **Delivers**: self-reinforcing evidence (agent A finds a deploy → agent B
  focuses on post-deploy metrics → more precise correlation).
- **Expected limitation**: the flow is still orchestrated by the supervisor;
  agents don't decide "I need to investigate X that no one asked for".
- **Promotion trigger to Level 4**: the system needs real autonomy — decisions
  without a human in the loop, auto-trigger, emergent hypotheses no individual
  agent would propose.
- **Cost**: each round with context = more tokens (N summaries × M agents).
  Validate ROI before advancing.

## Level 4 — Autonomous agents with multi-step reasoning

Agents propose hypotheses, delegate to each other, iterate until converging on an
RCA. Shared long-term memory. Auto-trigger (detects symptom → investigates
without waiting for a human). Convergence by voting/confidence, not a fixed round.

- **Delivers**: a system that solves emergent problems no previous level would.
- **Risks**: explosive token cost, infinite loops, incorrect decisions without supervision.
- **Mandatory guardrails**: per-investigation budget cap, max iterations,
  human-in-the-loop for actions (read-only for collection), kill switch.
- **Prerequisites**: Levels 1–3 validated + RCA quality metrics + controlled cost.

## Evolution principles

- **Each level proves value before advancing** — don't build Level 3 without
  evidence that Level 2 is insufficient.
- **Promotion triggers are measurable** — not "seems like we need it", but "in
  X% of cases, Y failed due to Z".
- **Cost is a real constraint** — each level multiplies tokens. Measure
  $/investigation at each level.
- **Read-only is the posture** — at all levels, agents collect and analyze. They
  never execute a fix automatically (without a human approving). (Note: read-only
  is the current posture, not eternal — see `docs/READ_ONLY_POLICY.md`.)
