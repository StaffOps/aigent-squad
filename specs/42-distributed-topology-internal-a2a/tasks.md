# Tasks: Distributed topology — internal A2A

Legend: `[ ]` pending · `[~]` partial · `[x]` done.

> **Status 2026-08-11 (not-started):** this task plan documents the full commitment so
> a reader sees the size BEFORE starting. The spec is `status: not-started` — writing
> it is **not** a decision to build. The dormant trigger's third condition (CI
> green 30 consecutive days) is ~1 day old. No task below should begin until all
> three trigger conditions hold simultaneously AND the team explicitly decides
> to proceed.

---

## Commitment overview — status table

Reused from the dormant entry's 11-step/4-phase path. Roughly two thirds of the
work is prerequisite infrastructure that has nothing to do with A2A itself.

| Phase | Steps | Theme | A2A-specific? | Promotion trigger |
|-------|-------|-------|---------------|-------------------|
| **A** | 1–5 | Earn the right (ops maturity) | No | All 5 steps validated in staging for ≥7 days |
| **B** | 6–8 | Security surface | Partially (inbound/outbound on A2A messages) | Round-table (security + sre) approves threat model |
| **C** | 9–10 | The protocol itself | Yes | Agent Card serves correctly; Task round-trip matches in-process parity |
| **D** | 11 | Prove it | Yes | One real distributed query homologated end-to-end in a live environment |

**Shape**: steps 1–8 are worth doing whether or not A2A is ever adopted (they
improve resilience for the in-process topology too). A2A itself is steps 9–10.
If Phase A is unaffordable, that is the answer about A2A.

---

## Phase A — Earn the right (5 steps, none A2A-specific)

- [ ] T1: **Per-agent SLOs + error budgets.** Extend spec 33 review loop to
  define per-agent availability/latency targets. Emit `aigent.agent.slo_budget_remaining`
  metric. Acceptance: each agent has a measured SLO for ≥14 days before Phase B
  starts.

- [ ] T2: **Cross-agent distributed tracing.** Propagate `traceparent` across
  the supervisor→specialist hop. Today the loop is in-process so this has never
  been exercised. Acceptance: a fan-out query produces a single trace visible in
  the OTel collector spanning gateway → supervisor → N specialists → Bedrock,
  with per-specialist spans.

- [ ] T3: **Cost guardrail that survives a network hop.** Two parts, in order —
  the first was missed in the original draft because it wrongly assumed the
  budget was already Redis-backed:
  - [ ] T3a (**prerequisite, do first**): migrate `token_budget.py` off its
    in-memory `dict[str, int]` + `threading.Lock` (`src/core/token_budget.py:25`)
    onto a shared store, keeping the `check_budget` / `record_usage` contract
    intact so call sites do not churn. Decide and document the Redis-outage
    failure mode explicitly: the rate limiter fails **open**, but a budget that
    fails open is an unbounded-spend path, so this should fail **closed**.
    Acceptance: two replicas sharing one `session_id` enforce ONE budget, proven
    by driving both concurrently — today each would enforce its own, silently
    permitting N× the intended spend. Note this hole is already latent for the
    multi-replica **gateway**; distribution widens it rather than creating it.
  - [ ] T3b: a remote specialist reports consumed tokens in its response
    metadata, and the supervisor atomically increments the shared session bucket
    before dispatching the next agent. Acceptance: a 3-agent fan-out with a tight
    budget (e.g. 50K tokens) correctly refuses the 3rd agent if agents 1+2 already
    consumed it.

- [ ] T4: **Partial-failure semantics for fan-out.** Add per-agent timeout
  (configurable), circuit breaker per remote agent, and verify the synthesizer
  degrades gracefully. Acceptance: killing one specialist mid-request produces
  a degraded-but-useful synthesis citing the failure (same contract as the
  current `agents_failed` field). Test with chaos: `docker compose kill
  aws-agent` during a fan-out.

- [ ] T5: **Rollback + chaos drill for one specialist as an independent
  deployable.** Extract ONE agent (e.g. `finops`, lowest traffic) into a
  separate container in docker-compose. Prove: deploys independently, rolls
  back independently, supervisor detects failure and degrades. Acceptance:
  documented runbook, chaos drill passing 3 consecutive runs.

### Phase A promotion trigger

All 5 steps validated in staging (docker-compose or dev cluster) for ≥7
consecutive days with no regressions in the existing `make test` suite.
Failure of any step → do not proceed to Phase B.

---

## Phase B — Security surface (3 steps)

- [ ] T6: **Inbound path controls.** Apply `InputScanner` + Guardrail on Task
  messages arriving at the specialist from the supervisor, fail-closed. Same
  tagging discipline as `<infra_data>` for any content not originated by a
  human. Acceptance: a crafted injection in the Task message body is caught and
  returns 403 to the supervisor (which propagates to the user as
  GuardrailBlockedError).

- [ ] T7: **Outbound path controls.** Apply `OutputFilter` over Artifacts the
  specialist emits. The supervisor additionally filters the received Artifact
  before synthesis (double-filter, defense-in-depth). Explicit decision (per
  `docs/READ_ONLY_POLICY.md`): a remote specialist receiving a write-implying
  request refuses it locally — the supervisor NEVER proxies a write. Acceptance:
  an Artifact containing a test canary token is caught by the supervisor's
  output filter.

- [ ] T8: **Per-remote-agent authn/authz + rate limiting.** Mutual
  authentication (bearer token initially; mTLS as a promotion target).
  Per-specialist rate limit in Redis (prevents a compromised supervisor from
  flooding a specialist). Acceptance: a request without valid token → 401; a
  burst exceeding rate limit → 429.

### Phase B promotion trigger

Round-table review (security + sre subagents) **approves** the threat model
for internal A2A. Written sign-off that objection 1 (dormant entry) is
sufficiently mitigated for internal use. Failure → do not proceed to Phase C.

---

## Phase C — The protocol itself (2 steps, the cheap part)

- [ ] T9: **Agent Card generation from `agent.yaml`.** Additive fields ONLY —
  `capabilities` remains `list[str]`, `routing_keywords` remains flat. A new
  optional `a2a:` block carries URL, auth mechanism, health path, timeout.
  A build-time script generates the A2A Agent Card JSON from these fields.
  Acceptance: `test_classifier.py` passes unmodified (keyword routing intact);
  the generated card serves at the well-known path with correct capabilities.

  > **HARD CONSTRAINT**: any renaming or nesting of `capabilities` or
  > `routing_keywords` breaks keyword routing for every agent
  > (`src/core/classifier.py:222`).

  > **UNVERIFIED**: the exact well-known discovery path and the current A2A spec
  > version. Confirm against `github.com/a2aproject/A2A` primary source before
  > implementing.

- [ ] T10: **A2A server + client.** Specialist: thin HTTP server accepting Tasks
  (JSON-RPC 2.0), delegating to `GenericAgent.process_request()`, returning
  Artifacts via SSE. Supervisor: `a2a_client.py` sends Tasks, reads SSE stream,
  maps to existing `AgentResponse`. Circuit breaker per agent. Acceptance: a
  distributed fan-out (supervisor → 2 specialists in separate containers)
  returns the same answer (content-equivalent, not byte-identical) as the
  in-process fan-out for the same query.

### Phase C promotion trigger

A2A round-trip (supervisor → specialist → response) matches in-process
parity for ≥10 golden queries from `evals/golden_queries.yaml`. Eval score
does not regress.

---

## Phase D — Prove it (1 step)

- [ ] T11: **One real distributed query homologated end-to-end in a live
  environment.** Not a unit test. Deploy at least one specialist as an
  independent service in devops-core (or equivalent staging), route real traffic,
  and verify: answer quality, latency, tracing, budget accounting, degraded
  synthesis on kill. Acceptance: documented evidence (screenshots/logs/traces)
  of a real query served via the distributed path with no regression vs the
  in-process path.

  > Today's lesson stands: spec 41 passed 56 tests and was inert in production
  > until someone ran a real query.

### Phase D promotion trigger

Homologation evidence reviewed and accepted. Spec status moves from `not-started`
→ `in-progress` → `done` only after D is complete.

---

## Verification-independence pipeline

Per `specs/README.md`:

- [ ] T12: Independent test authorship — a different session writes/reviews tests
  for Phases A–D.
- [ ] T13: Independent code review — fresh review pass against steering, security,
  spec Acceptance Criteria.
- [ ] T14: Security review — mandatory (spec touches auth, trust boundary,
  data egress path between services).

---

## Notes

- This task plan reuses the dormant entry's 11-step/4-phase path verbatim.
  The steps above (T1–T11) correspond to that entry's steps 1–11.
- The total commitment is **significant** — roughly 6–8 weeks of focused work,
  most of which is Phase A operational maturity (useful regardless of A2A).
- At any point, if the dormant trigger conditions cease to hold (e.g. CI goes
  red, or the external-system need disappears), work pauses and the spec
  returns to dormant.
