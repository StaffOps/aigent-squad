# Design: Multi-Agent Collaboration

## Architecture

Maintains hub-and-spoke (supervisor coordinates), but the hub now performs **fan-out/fan-in** when the query is cross-domain:

```
                         ┌─ aws-agent ──────┐
User → Supervisor → Classifier (1..N) ─┼─ finops-agent ───┤→ Synthesizer → single response
                         └─ observability ──┘   (1 Bedrock call)
                         (asyncio.gather, parallel)
```

- **N=1** (common case): fast-path — calls 1 agent and returns directly. **Zero** synthesis cost.
- **N≥2**: calls agents in parallel, collects responses, synthesizes.
- **Agent-as-tools**: orthogonal to fan-out — an agent, while processing, can request data from another (1 hop), reusing the same `/process` endpoint with a depth header.

## Components

| Component | Responsibility | Location |
|-----------|----------------|----------|
| Classifier (multi) | Return ordered list of relevant agents | `src/core/classifier.py` (extends contract) |
| Orchestrator (fan-out) | `asyncio.gather` of N agents + partial failure tolerance | `src/supervisor/agent.py` |
| Synthesizer | 1 Bedrock call that merges N responses → 1, with attribution | `src/supervisor/synthesizer.py` (new) |
| Hop guard | Header `X-Agent-Hop` limits agent-as-tools to depth=1 | `src/core/agent_base.py` + servers |
| Agent tool-call | Thin client for one agent to call another via supervisor | `src/core/agent_tools.py` (new) |

## Classifier contract (backward-compatible)

```python
@dataclass
class ClassifierResult:
    agents: list[AgentMatch]        # NEW — ordered by relevance
    reasoning: Optional[str] = None
    @property
    def selected_agent(self) -> str:  # compat: first in the list or "unknown"
        return self.agents[0].agent if self.agents else "unknown"

@dataclass
class AgentMatch:
    agent: str
    confidence: float
```

The classifier's system prompt now allows 1..N agents and explains when to use more than one (multi-faceted query) vs a single one (follow-up, single domain). `max_agents` (default 3) truncates the list.

## Fan-out (fan-in)

```python
# supervisor, when len(agents) >= 2
async def _fan_out(self, agents, payload):
    tasks = [self._call_agent(a.agent, payload) for a in agents]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    ok = [(a.agent, r) for a, r in zip(agents, results) if not isinstance(r, Exception)]
    failed = [a.agent for a, r in zip(agents, results) if isinstance(r, Exception)]
    return ok, failed   # partial failure does not bring down the query
```

Total time ≈ `max()` of latencies, not the sum. Reuses the circuit breaker and timeouts from spec 06.

## Synthesis

A single Bedrock call receives: the original query + the N responses labeled by agent, and produces the final response preserving attribution. Uses **Sonnet** model (quality of the merge matters), while the classifier uses **Haiku** (spec 11). If `failed` is non-empty, the prompt instructs to note the degradation.

## Agent-as-tools (depth = 1)

```
agent A (processing) ── needs data from B ──▶ POST /process (X-Agent-Hop: 1)
agent B responds ──▶ A uses it in its context ──▶ A's response
```

- `X-Agent-Hop` absent/0 = user call; `=1` = agent-to-agent call; `≥2` = **rejected** (breaks cycles).
- Read-only preserved: B is the same consultative agent as always.
- Exposed as an optional tool; an agent only uses it when the prompt detects a cross-domain need.

## Rationale (decisions and trade-offs)

### Decision 1: Fan-out + synthesis in the supervisor (not a free agent-to-agent mesh)

**Choice**: multi-domain collaboration is orchestrated by the supervisor (fan-out/fan-in), not by agents freely conversing with each other.

**Justification, in order of strength**:
1. **Cost and termination control**: a free agent↔agent mesh has no natural hop limit → explosion of Bedrock calls and cycle risk. Fan-out at the hub has a deterministic ceiling (`max_agents`, 1 synthesis).
2. **Observability**: 1 tree-shaped trace (supervisor → N leaves → synthesis) is readable; an arbitrary call graph is nearly impossible to debug.
3. **Reuse**: the supervisor already has an HTTP client, circuit breaker (spec 06), and history — the fan-out reuses all of it.

**Accepted trade-offs**:
| Cost | Reality |
|------|---------|
| No "debate" between agents (1 round only) | Covers 90% of cross-domain cases; debate is over-engineering for a consultative ChatOps |
| Synthesis adds 1 Bedrock call when N≥2 | Only in the multi-domain case; N=1 (majority) does not pay for it |

**When this decision would be wrong** (signals to reopen):
- Real cases require multiple rounds (A responds, B refines, A reconsiders) frequently.
- The `max_agents=3` ceiling proves insufficient for real queries.

**Alternatives discarded**:
- **P2P agent-to-agent mesh** — discarded due to cost/cycles/observability.
- **LangGraph/orchestration framework** — discarded: was deliberately removed from the project (see steering `project.md`); reintroducing it contradicts a standing decision.

### Decision 2: Agent-as-tools limited to 1 hop

**Choice**: an agent can call **at most one** other agent, never chained.

**Justification**:
1. **Anti-cycle**: depth=1 makes cycles impossible by construction (A→B, B cannot call anyone else).
2. **Predictable latency/cost**: worst case = 2 agents + 1 synthesis, not an indefinite chain.
3. **Testing simplicity**: the state space is small and enumerable.

**Accepted trade-offs**:
| Cost | Reality |
|------|---------|
| Chains A→B→C are impossible | If C is needed, the supervisor's fan-out (Decision 1) already includes it in parallel |

**When it would be wrong**: if legitimate 2+ hop dependencies emerge that the fan-out cannot resolve.

### Decision 3: Multi-agent classifier backward-compatible (list, with derived `selected_agent`)

**Choice**: extend `ClassifierResult` to a list and expose `selected_agent` as a property (first item).

**Justification**: does not break the current supervisor or specs 02/06 while the fan-out is introduced; the N=1 path remains identical. Incremental migration.

**Trade-off accepted**: one derived field to maintain — trivial cost compared to a breaking change in the contract.

## Invariants

- N=1 **never** triggers synthesis (immutable fast-path).
- `X-Agent-Hop ≥ 2` is always rejected.
- Partial failure in the fan-out **degrades**, does not bring down the query.
- Read-only preserved in all paths (fan-out and agent-as-tools).
- `max_agents` ceiling applied **before** any call (cost protection).

## External dependencies

| Service | Usage |
|---------|-------|
| Bedrock | classifier (Haiku) + agents + synthesis (Sonnet) |
| (inherited) | `/process` endpoints of the 5 agents |

## Verification

```bash
docker run --rm -v $(pwd):/app -w /app python:3.11-slim sh -c \
  "pip install -q -r requirements.txt pytest pytest-asyncio && pytest tests/ -v --cov=src --cov-fail-under=90"
```

Key tests (test-author ≠ code author): classifier returns N agents for a multi-domain query; `asyncio.gather` runs in parallel (assert time ≈ max, with mocked agents + sleep); synthesis merges N→1; partial failure includes degradation note; `X-Agent-Hop=2` returns rejection; N=1 does not call the synthesizer.

## Risks

- Cost: fan-out multiplies Bedrock calls. Mitigated by `max_agents` + Haiku for the classifier + circuit breaker.
- Synthesis quality: poorly calibrated prompt merges responses confusingly. Mitigate with examples in the prompt + explicit attribution.
- Async prerequisite (spec 06): without it, `asyncio.gather` does not provide real parallelism (synchronous boto3 blocks the loop).

---

## Extensibility: shared context (Level 3+ of the ROADMAP)

> This section documents how the fan-out evolves without rewriting. DO NOT implement in Level 1–2.

**Level 3 problem**: agents collect independently; each one does not know what the others found. This limits quality when one agent's evidence WOULD CHANGE another's query.

**Solution**: `asyncio.gather` accepts a **scratchpad** (spec 18 `InvestigationState`) as context injected into the agents' prompts in subsequent rounds.

```python
# Level 1-2: simple fan-out
results = await asyncio.gather(*[call_agent(a, payload) for a in agents])

# Level 3+: fan-out WITH shared context
for round in range(max_rounds):
    context = scratchpad.summary()  # summary of previous rounds
    enriched_payload = {**payload, "prior_evidence": context}
    results = await asyncio.gather(*[call_agent(a, enriched_payload) for a in agents])
    scratchpad.update(results)
    if synthesizer.evidence_sufficient(scratchpad):
        break
```

**What changes in the agents' contract**: they receive an optional `prior_evidence` field (string, summary). Agents that do not support it (Level 1) ignore the field. Zero breaking change.

**Promotion trigger**: "context from other agents would improve collection in >20% of cases" (measured by the diff in RCA confidence with/without context in A/B tests).
