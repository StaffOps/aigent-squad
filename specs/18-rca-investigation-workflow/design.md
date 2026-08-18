# Design: RCA Investigation Workflow

## Architecture

An **investigation orchestration** layer on top of spec 17's fan-out. The supervisor gains an "investigate" mode that follows a deterministic cycle:

```
Symptom → [decision: trivial? → fast-path]
            │ non-trivial
            ▼
   1. Plan time window + relevant agents (config-driven)
   2. PARALLEL fan-out of evidence collection (spec 17)
        ├─ observability: metrics/logs/traces in the window
        ├─ kubernetes:    events, restarts, OOMKills
        ├─ devops:        deploys/MRs in the time period
        └─ aws:           infrastructure health (RDS/cache/nodes)
   3. Normalize → Evidence[] (with strength)
   4. Build Timeline (ordered, cause before effect)
   5. Correlate (≥3 independent signals → strong hypothesis)
   6. Synthesize RCA (1 Bedrock call) + propose prevention
```

## Investigation triggers

The investigation can be triggered by **two sources** (decision 2026-06-02):

| Trigger | Endpoint | Payload |
|---------|----------|---------|
| **User** (chat) | `POST /chat` with `mode=investigate` or automatic symptom detection | Free text |
| **Alertmanager** (webhook) | `POST /alerts/incoming` | Alertmanager v2 JSON (alerts[], groupLabels, commonLabels) |

The `/alerts/incoming` endpoint lives in the **supervisor** (which is already FastAPI). Does not require a new service. The alert is converted to a structured symptom (severity, service, timestamp, labels) and enters the same investigation cycle.

## Rounds and scratchpad

**max_rounds** (config-driven, default=5): maximum number of collection rounds per investigation. Each round, the synthesizer evaluates whether evidence is sufficient or more is needed. The ceiling is a hard stop (not a recommendation).

| Level (ROADMAP) | max_rounds | Context between rounds |
|-----------------|------------|------------------------|
| 1 (MVP) | 1 (fixed) | — |
| 2 (iterative) | 5 | Synthesizer identifies gaps → 2nd+ directed round |
| 3 (shared) | 10 | Agents receive summary from others via scratchpad |
| 4 (autonomous) | 25 | Agents propose hypotheses + delegate |

**Scratchpad** (short-term memory per investigation):
- Dict/Redis hash keyed by `investigation_id`
- Contains: evidence collected so far, current hypotheses, identified gaps
- Lifetime: duration of the investigation (deleted on completion)
- From Level 3 onwards, the scratchpad is injected into agents' prompts in the next round

```python
@dataclass
class InvestigationState:
    id: str
    symptom: str
    rounds_completed: int
    max_rounds: int
    evidence: list[Evidence]
    hypotheses: list[str]
    gaps: list[str]  # "what is still missing to collect"
```

## Components

| Component | Responsibility | Location |
|-----------|----------------|----------|
| Investigation orchestrator | Cycle above; decides trivial vs investigate; controls rounds | `src/supervisor/investigation.py` (new) |
| Alert ingestion | Converts Alertmanager payload → structured symptom | `src/supervisor/alert_handler.py` (new) |
| Evidence model | Normalized evidence structure | `src/core/investigation.py` (new) |
| Timeline builder | Orders events, marks cause candidates | `src/core/investigation.py` |
| Correlator | Confidence rule by number of independent signals | `src/core/investigation.py` |
| RCA synthesizer | Merges evidence → RCA + prevention; detects gaps for next round | `src/supervisor/investigation.py` |
| Scratchpad | Per-investigation state (evidence, hypotheses, gaps) | `src/core/investigation.py` (in-memory or Redis) |

> Agents **do not change responsibilities**: they remain consultative and read-only. What changes is the supervisor passing a *evidence collection intent* (window + focus) instead of a free question.

## Models (contract)

```python
@dataclass
class Evidence:
    source_agent: str
    signal_type: str        # metric | log | trace | event | deploy | infra
    timestamp: str          # ISO; "" if non-temporal
    strength: str           # strong | medium | weak  (see hierarchy below)
    summary: str

@dataclass
class RCAResult:
    hypothesis: str
    confidence: str         # high | medium | low
    evidence: list[Evidence]
    timeline: list[Evidence]      # temporal subset, ordered
    contradicting: list[Evidence] # counter-evidence (do not discard)
    prevention: list[str]         # alert/test/guardrail/runbook
```

### Evidence model and correlation → see `../EVIDENCE-MODEL.md`

The complete signal catalog (33 signals C1-C8/M1-M13/I1-I8/T1-T4/E1-E4), the 14 root-cause signatures, the confidence algorithm, and the independence test live in **`specs/EVIDENCE-MODEL.md`** (observability+sre+troubleshoot deliberation, 2026-06-02). Summary of what changes here:

**Correlation by CAUSAL LAYERS, not by signal count:**
```
CHANGE (what changed) + MECHANISM (how it caused) + IMPACT (damage) + valid temporal order
  + 0 unexplained counter-evidence  →  HIGH confidence
```
- **Track A** (event-driven): CHANGE+MECHANISM+IMPACT.
- **Track B** (degradation without explicit change): continuous MECHANISM (e.g., monotonic memory growth) + confirming MECHANISM + IMPACT + no alternative CHANGE.

**Real independence** (solves the naive "≥3 signals"): two signals derived from each other (OOMKill→restart, error_rate↔error_log) count as ONE. Exemplar metric→trace→log from the same request is depth (proof quality), not breadth (count). Signals from the same layer+same fault_domain are dependent by default.

**Timestamps**: CloudWatch delays up to 120s — never anchor causal order. Metrics (15s) do not resolve sub-15s cascades — use Loki `direction=forward&limit=1` per service. Per-source-pair timing tolerances in `EVIDENCE-MODEL.md §5`.

**LLM confidence**: ceiling (synthesizer never increases) + flexible floor (can lower with logged justification).

## Trivial vs investigate decision

Explicit threshold (cheap, no extra Bedrock call): if the symptom matches a single-domain pattern with a known fix (e.g., "typo in the YAML", "how do I configure X") → fast-path (1 agent). Otherwise, or if the user uses `mode=investigate`, opens an investigation. Follows `investigation-protocol` ("obvious cause in <30s? → direct fix").

## Rationale (decisions and trade-offs)

### Decision 1: RCA as orchestration over fan-out (not a new "RCA agent")

**Choice**: investigation is a **mode of the supervisor** that reuses the 5 existing agents as evidence collectors; we do not create a 6th "troubleshooter" agent.

**Justification, in order of strength**:
1. **The agents already have access to the signals** (observability→metrics, k8s→events, devops→deploys). A new RCA agent would duplicate those accesses and each datasource's logic.
2. **Product simplicity**: fewer components to operate/configure. The user asked for "not overly complex."
3. **Direct reuse** of fan-out (17) and config (19) — the investigation is a *composition*, not a new piece.

**Accepted trade-offs**:
| Cost | Reality |
|------|---------|
| The supervisor becomes "heavier" (gains the investigation layer) | It is cohesion, not coupling — the correlation logic does not belong to any domain agent |
| Correlation is centralized | Correct: only the supervisor sees all signals; distributed correlation would require shared state |

**When it would be wrong** (signals to reopen): if investigation logic grows to the point of having its own lifecycle/scaling — then it becomes its own service.

### Decision 2: 1 collection round (not iterative investigation loop) in Phase 1

**Choice**: Phase 1 does **one** round of fan-out → correlation → RCA. There is no "collect more evidence based on the 1st hypothesis."

**Justification**:
1. **Predictable latency and cost** — iterative investigation multiplies Bedrock calls without a clear ceiling.
2. **Covers the majority** of cross-domain RCA cases with 1 good parallel round.
3. **Avoids premature complexity** before the product proves its value.

**Trade-off accepted**: cases that require deepening ("the 1st round suggests memory leak → now I need the heap profile") are left for Phase 2. Marked in `tasks.md` with a promotion trigger.

**When it would be wrong**: if in practice the majority of RCAs need 2+ rounds to conclude.

### Decision 3: Absolute read-only — proposes prevention, never remediates

**Choice**: the investigation ends in a **proposal** of prevention; it never executes a fix.

**Justification**: the project's read-only invariant (4 layers) + security. A wrong RCA executing automatic remediation is the worst possible scenario for a product.

**Trade-off accepted**: the user still applies the fix manually — acceptable and desirable for a consultative product.

## Invariants

- Investigation is **read-only** (only reads signals; prevention is text, not an action).
- Trivial symptom does **not** open an investigation (fast-path).
- Counter-evidence is always recorded and affects confidence.
- Cost ceiling (number of agents, number of evidence queries) applied **before** calls (config — spec 19).
- Confidence never "high" with <3 independent signals.

## External dependencies

| Service | Via agent |
|---------|-----------|
| VictoriaMetrics/Prometheus, Loki, Tempo | observability |
| Kubernetes API (events) | kubernetes |
| GitLab (deploys/MRs) | devops |
| CloudWatch/infra | aws |
| Bedrock (RCA synthesis, Sonnet) | supervisor |

Datasources and default windows come from config (spec 19), not hardcoded.

## Verification

```bash
docker run --rm -v $(pwd):/app -w /app python:3.11-slim sh -c \
  "pip install -q -r requirements.txt pytest pytest-asyncio && pytest tests/ -v --cov=src --cov-fail-under=90"
```

Key tests (test-author ≠ author): parallel collection with mocked agents; timeline orders by timestamp and marks the deploy as a candidate; correlation returns "high" with 3 signals and "low" with 1; counter-evidence downgrades confidence; trivial symptom does not call fan-out.

## Risks

- **Cost**: investigation = several agents + synthesis. Mitigated by config ceiling + fast-path + Haiku in the classifier (spec 11).
- **False confidence**: temporal correlation ≠ causality. Mitigated by the strength hierarchy + requiring ≥3 signals + recording counter-evidence.
- **Timestamp quality**: without precise timestamps there is no timeline. Depends on the quality of the signals the agents return (spec 09 helps).
