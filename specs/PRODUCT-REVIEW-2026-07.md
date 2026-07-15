# Product Review — 2026-07 (consolidated critical findings & orientations)

**Status**: frozen record (2026-07-04) — findings live on in `specs/BACKLOG.md` and the
referenced specs; this document preserves the full reasoning so the guidance is never lost.
**Method**: full read of all specs (81 files, 2026-07-03) + code/config verification +
agent-executability audit (2026-07-04).
**Companion decisions**: `docs/prd/aigent-squad.md` (approved), ADR-0007 (proposto).

Each finding: **ID · severity · evidence · orientation · where it is tracked**.

---

## A. The core verdict (unchanged)

**PR-01 · 🔴 · Platform/product inversion.** Infrastructure around the value is mature
(gateway, security L1–L6, tiering, CI, chart); the value itself — response quality and
RCA correctness — has zero measurement and near-zero real usage. Orientation: ~70%
value / 30% platform for the next quarter. → Tracked: ROADMAP work order (2026-07-04).

## B. NEW findings — functionality depth (this pass)

**PR-02 · 🔴 · Evidence collection for RCA is largely hollow.** Verified in config:
the observability agent's ONLY datasource is `http` → `${PROMETHEUS_URL}/api/v1/query?query=up`
— a **static, hardcoded query** returning target-up status regardless of the question.
No Loki (logs), no Tempo (traces), no query construction from the symptom. The devops
agent's datasources are bare-URL GETs (GitLab `/api/v4` base, docs portal root) — no
deploy-history/MR queries; ArgoCD (EVIDENCE-MODEL signal C1, the anchor of the #1
root-cause signature) is not wired at all. Consequence: of the EVIDENCE-MODEL's 33
signals, the CHANGE and MECHANISM layers — the ones that make an RCA *causal* rather
than descriptive — are mostly uncollectable today. The RCA currently runs on K8s events
+ AWS inventory + CE. **This is the single biggest gap between the product's design and
its capability**, bigger than the unimplemented correlator: a perfect correlator with
hollow evidence still produces shallow RCAs.
→ Orientation: (1) signal-coverage audit (map all 33 signals → collectable today
yes/no/how); (2) query-capable adapters: PromQL builder (metrics by service/window),
LogQL (Loki first-error/pattern queries), deploy-history (GitLab MRs/deployments or
ArgoCD API). Likely a dedicated spec (`37-evidence-adapters` candidate).
→ Tracked: spec 18 Phase 1.5 **T12a** (audit) + `BACKLOG.md` (adapter spec candidate).

**PR-03 · 🟠 · No streaming / progress UX.** `stream:true` is pseudo (full answer, then
one SSE chunk). finops takes ~17s, investigations more — the user stares at a blank
screen. For a chat product this is the #1 perceived-quality killer, independent of
answer quality. → Orientation: real token streaming (spec 06's promised `AsyncIterable`)
or, cheaper first step, progress events ("collected EC2 inventory… generating analysis").
Priority rises the moment an interface (LibreChat/Slack) goes live.
→ Tracked: BACKLOG (paired with the interface decision).

**PR-04 · 🟠 · No user feedback loop.** Nothing captures whether an answer helped —
no 👍/👎, no correction path. The KB learns from investigations (spec 21) but nothing
learns from users. Every quality signal today requires a human reading transcripts.
→ Orientation: `feedback` field on `/query`/`/v1` responses + `aigent.feedback.score`
metric + feed into spec 33 reviews. Cheap, high-signal.
→ Tracked: BACKLOG.

**PR-05 · 🟠 · Groundedness is unverified.** Agents synthesize over truncated infra
data; nothing checks that numbers/resource-IDs in the answer actually appear in the
collected context (hallucinated "640 instances" when the data said 695 would ship).
→ Orientation: groundedness check as a T1/T2 eval dimension (numeric/ID claims must
exist in `infra_data`) — the quality twin of the canary check.
→ Tracked: spec 35 (acceptance criterion added 2026-07-04).

**PR-06 · 🟡 · Always-collect-everything.** `GenericAgent` fans out to ALL of an
agent's adapters on every query ("what is an EC2?" still describes instances). Brute-
force context stuffing costs tokens/latency on every request.
→ Orientation: selective collection by query relevance (keyword/intent match per
datasource — NO tool-loop, ADR-001 intact). → Tracked: BACKLOG.

**PR-07 · 🟠 · The ecosystem's best trigger is unwired.** `staffops-anomaly-detection`
produces enriched alerts ("~80% of the diagnosis ready" — ECOSYSTEM.md) and spec 18 was
designed to consume that contract. It never was wired. This is the highest-leverage
integration available: it makes the squad valuable WITHOUT anyone asking a question.
→ Orientation: wire anomaly-detection → `/alerts/incoming` as the **CASE-001 pathway**
(spec 18 T15) — one integration, two goals. → Tracked: spec 18 Phase 1.5 T15 note + BACKLOG.

**PR-08 · 🟡 · Skills mechanism built, knowledge starved.** Lazy-skill machinery is
done (spec 26, 100% cov); content = 1 skill (oomkill). The cheap way to make answers
feel senior is encoding the team's real runbook knowledge — zero code required.
→ Orientation: skills content sprint — 5–10 SKILL.md from real BDC operational
knowledge (throttling, IRSA debugging, node pressure, cost spikes…). → Tracked: BACKLOG.

**PR-09 · 🟡 · 24h memory is a silent product decision.** DynamoDB TTL=24h means "what
did we find about service X last week?" returns nothing; only distilled investigations
persist (KB). May be the right call (cost/privacy) — but it should be a DECIDED
trade-off, not a default. → Orientation: decide + document (config already supports
another TTL). → Tracked: BACKLOG (decision item).

**PR-10 · 🟡 · Security overhead is unmeasured.** Every request pays: InputScanner +
guardrail(input) + guardrail(output) + output filter + canary — ×2 when classifier +
agent both invoke. Right posture (ADR-0004), unknown price. → Orientation: measure
ms/$ overhead as a TRIGGERS.md row (spec 33); optimize only with data.
→ Tracked: spec 33 T1 inventory (row added via BACKLOG note).

**PR-11 · 🟡 · No integration smoke in CI.** 93% unit coverage, but every compose-level
smoke task was deferred (specs 06/17/18 T11, 08 T7). Unit mocks won't catch a broken
compose wiring or env regression. → Orientation: nightly (not per-push) CI job:
compose up → `make smoke` (spec 36 provides both pieces). → Tracked: BACKLOG.

**PR-12 · ⚪ · Model-version refresh has no owner.** Model IDs are pinned in config
(correct) but nothing re-evaluates them as new Claude versions land on Bedrock.
→ Orientation: standing item in the spec 33 monthly review. → Tracked: spec 33
TEMPLATE (via BACKLOG note).

**PR-13 · ⚪ · Internal taxonomy leaks into UX.** LibreChat dropdown exposes
`aigent-squad-<agent>` — users think in problems, not in our agent domains. Harmless
now; revisit at interface work. → Tracked: BACKLOG (note on the interface item).

**PR-14 · ⚪ · Data sensitivity note missing.** History (DynamoDB) and KB (pgvector)
store real infra data; KB has PII redaction, history doesn't need it, but a one-paragraph
data-classification/retention note in SECURITY.md closes the question. → Tracked: BACKLOG.

## C. Prior findings (2026-07-03/04 sessions — already tracked, listed for completeness)

| ID | Finding | Tracked |
|----|---------|---------|
| PR-15 🔴 | RCA differentiator never validated on a real incident | spec 18 T15 (CASE-001) |
| PR-16 🔴 | EVIDENCE-MODEL designed to replace naive correlator, never implemented | spec 18 T12–T14 |
| PR-17 🔴 | Two live quality defects (aws `<use_mcp_tool>` echo = F-001; finops AccessDenied surfaced in every answer = F-002) | BACKLOG F-001/F-002 + spec 35 T1 regression fixtures |
| PR-18 🔴 | No response-quality gate (security has one, quality doesn't) | spec 35 |
| PR-19 🔴 | No user-reaching interface (curl only); flywheel has no crank | ADR-0007 + interface decision (PRD open question) |
| PR-20 🟠 | Spec-14 entry-point findings A/B/C/D open | spec 14 tasks (gates 0.4.0) |
| PR-21 🟠 | Agents are shells relative to the vision (devops lost gitlab intelligence; placeholders) | spec 35 T5 capability matrix + PR-02 |
| PR-22 🟠 | Internal vs OSS identity undecided (private dep blocks external use) | ADR-0007 (proposto) |
| PR-23 🟡 | Agent-executability friction (5 blockers) | spec 36 |
| PR-24 🟡 | Process debt: status drift, no findings home, no ops loop, artisanal releases | specs 32/33/34 |

## E. Round 3 (2026-07-04) — residual limits ASSUMING all of the above ships

> Forward-looking: even with evidence adapters, the correlator, evals, streaming,
> feedback and an interface all live, these are the ceilings the current DESIGN keeps.
> Framing constraint: smarter/more assertive at **net-neutral or negative cost** — the
> savings items (PR-27) fund the intelligence items (PR-25/29).

**PR-25 · 🔴 · Cognition is fixed-depth: one round, always.** The flow (classify →
collect per fixed plan → correlate → synthesize) cannot ask a follow-up question OF THE
DATA. Level 2 exists in the vision but framed as an expensive mode. The cost-shaped
version: **gap-directed round 2** — the correlator (rules, $0) already knows which
causal layer is missing (e.g. "CHANGE evidence absent"); trigger ONE targeted
re-collection (1–2 agents, refined sub-question, hard cap), not a full 5-agent round.
Marginal cost ≈ +$0.02–0.04 on the ~10% of investigations that need it.
→ Orientation: implement as the Level-2 MVP with rule-based (not LLM) gap trigger +
round cap 2. → Tracked: BACKLOG B-13.

**PR-26 · 🔴 · The classifier routes, but doesn't PLAN.** Every selected agent receives
the same raw query. "Why did cost rise after the k8s deploy?" should become: devops →
"deploys of X in window W"; finops → "cost delta after T"; k8s → "changes at T". One
cheap upgrade to the SAME Haiku call (extended JSON: per-agent sub-question + time
window) makes collection targeted — better answers AND fewer tokens (focused context
beats brute-force stuffing; compounds with PR-06 selective collection).
→ Orientation: classifier output schema gains `sub_queries[]`; GenericAgent receives
its sub-question. Near-zero cost. → Tracked: BACKLOG B-14.

**PR-27 · 🟠 · No cost-aware execution tiers — the savings engine.** Triage
(trivial-vs-investigate) exists but everything still pays full price: adapters + Sonnet
+ 2× guardrail evaluations even for "what is EC2?". Orientation: (a) triage class maps
to execution tier — trivial/conceptual → Haiku, zero adapters; standard → current
path; investigation → full; (b) guardrail INPUT evaluated once per unique text per
request (hash-keyed) instead of per-invoke (~50% fewer guardrail calls, fail-closed
intact). At 200 q/day (~60% trivial) this SAVES more than PR-25/29 ever spend —
the whole round funds itself. → Tracked: BACKLOG B-15.

**PR-28 · 🟠 · Assertiveness = calibrated honesty, and answers have none.** Being
assertive isn't only being right — it's being FIRM where grounded and EXPLICIT where
not. The groundedness check (PR-05) already computes which claims lack backing; today
that would only fail evals. Surface it in-line: response contract gains `confidence` +
`unverified_claims[]`, prompts instructed to state verified facts declaratively and
flag gaps ("could not verify X — IRSA denied athena"). Zero extra LLM cost — reuses
eval machinery at answer time. → Tracked: BACKLOG B-16.

**PR-29 · 🟠 · No environment model — every query rediscovers the world.** Agents see
raw API responses per request; nothing persists "what this environment IS" (services,
namespaces, ownership, cost drivers, normal baselines). A **periodic environment
snapshot** (cron: summarize inventory + baselines — reuse anomaly-detection's baselines
— into a compact cached context block) makes answers sound situated ("cluster X's
node group Y, which grew 20% this week…") and CUTS live API calls. Cost: ~1
summarization call/day, cached in Redis/KB. → Tracked: BACKLOG B-17.

**PR-30 · 🟡 · No diff primitive.** "Before/after deploy", "week-over-week cost" force
the LLM to diff two raw states in-prompt — token-heavy and error-prone. Adapters gain
`collect_diff(query, window_a, window_b)` computed in CODE, feeding the LLM a compact
delta. Deterministic where determinism is cheap; LLM only interprets. Cheaper AND more
precise. → Tracked: BACKLOG B-18.

**PR-31 · 🟡 · Knowledge compounds only from incidents, not from usage.** Spec 21
distills investigations; the daily Q&A stream (once the interface lives) carries gold —
corrections, environment facts, repeated questions. Route thumbs-down + correction
through the EXISTING KbDelta pipeline (extractor → validator → review), gated by the
existing budget guard. Closes the flywheel for Q&A. → Tracked: BACKLOG B-19 (extends B-03).

**PR-32 · 🟡 · Prevention is prose; it could be artifacts.** RCA `prevention[]` returns
generic text. Emitting ready-to-review ARTIFACTS (alert-rule YAML, runbook stub, k6
check) is the assertive version — propose-as-code, never applied (read-only intact).
Plus: the synthesizer merges with attribution but doesn't ADJUDICATE conflicts between
agents (A says healthy, B says degraded) — one prompt upgrade, same call.
→ Tracked: BACKLOG B-20.

**Consciously rejected (recorded so it's not re-proposed):** semantic/LLM response
caching — violates the "never cache LLM responses" invariant (cross-user leak,
multi-turn breakage); the datasource cache + PR-27 tiers capture the savings safely.

### Cost arithmetic (the net-neutral claim, @200 q/day)

| Item | Effect |
|------|--------|
| PR-27a tiering (~60% queries skip adapters+Sonnet) | ≈ −$2.0–2.5/day |
| PR-27b single guardrail input-eval | ≈ −15–20% guardrail spend |
| PR-26 focused context (smaller prompts) | ≈ −10–20% input tokens on routed queries |
| PR-25 gap-directed round 2 (~10% of investigations) | ≈ +$0.3/day |
| PR-29 daily snapshot summarization | ≈ +$0.05/day |
| **Net** | **negative (saves money) while adding depth** |

## F. Round 4 (2026-07-04) — on-trigger comprehension (reactive by design)

> **Product decision D-03 (owner: Carlos, 2026-07-04, refined same day)**: the squad is
> **reactive by design** — it acts ONLY when triggered (user question or incoming
> alert). Proactive watching/alerting is a **SEPARATE product** (that is
> `staffops-anomaly-detection`'s territory; its integration stays optional — enriched
> alerts arrive at `/alerts/incoming` like any other trigger). What IS core: when
> triggered, the squad must **minimally understand what it received** — which entity,
> what the data means, whether the behavior is normal. The methodology the user asked
> for ("how/what/why to define, which metrics") is **`docs/BEHAVIOR-BASELINES.md`**:
> worksheet W1–W8, signal catalog per entity class, baseline decision tree, the verdict
> contract, comprehension metrics, reactive rollout.

**PR-33 · 🔴 · The squad hands RAW numbers to the LLM and hopes.** Asked "is checkout
slow?", it fetches p99=840ms and the model *guesses* whether that's bad — it has no
notion of what 840ms means for THAT service at THAT hour. Assertiveness requires
**verdicts computed in code at request time**: now vs baseline (robust-z / seasonal /
static / derivative) → `normal | borderline | abnormal | no-baseline`, injected as
context. The LLM interprets judgments; statistics make them. Zero LLM cost — window
queries + arithmetic. → Orientation: spec candidate `38-on-trigger-comprehension`
(requirements seed = BEHAVIOR-BASELINES.md). → Tracked: BACKLOG B-22.

**PR-34 · 🔴 · No entity resolution — it doesn't know what a mention refers to.**
"checkout" must resolve to: k8s workload `checkout` (ns `shop`) + Prometheus
`service_name="checkout"` + cost tag — one entity, three datasource identities, plus
tier/owner. Without the alias map, agents query blindly and cross-domain evidence
doesn't join. Config-driven **entity registry** (`entities/` or `watchlist.yaml`,
same directory-as-config pattern as agents/skills). → Tracked: BACKLOG B-21.

**PR-35 · 🟠 · "What is normal" was undefined — now methodological, pending
implementation.** BEHAVIOR-BASELINES.md §§2–4 answer the definition problem: worksheet
(W1–W8, including W8 declared blind spots), signal catalog with mandatory
failure-hypothesis column, 4-method baseline decision tree (static / robust-z
median+MAD / seasonal-naive / derivative — ML never by default; a signal defeating all
four is the ONLY door through which anomaly-detection's ML earns a place, as a baseline
SOURCE, not a trigger). → Tracked: B-22 + the living doc.

**PR-36 · 🟠 · Comprehension needs quality metrics of its own.** Verdict precision
(operator 👍/👎 on verdict-backed answers, ≥80%), verdict coverage, **honesty rate**
(blind spots declared as `no-baseline`, 100%, eval-checked), baseline freshness. No
alert budgets — nothing here pages anyone. → Tracked: BACKLOG B-23 (+ TRIGGERS rows).

**PR-37 · 🟡 · Verdicts and investigation must speak ONE language.** Every verdict maps
to an EVIDENCE-MODEL signal id (layer + strength pre-filled) and enters `Evidence[]`
directly — comprehension feeds the correlator judged evidence, not prose. Catalog gaps
found: disk %, cost signals, quota headroom lack EM ids → extend as profiles grow.
→ Tracked: BACKLOG B-24.

**PR-38 · 🟡 · Validate on ourselves first.** Entity #1 in the registry = the squad
itself; "is the gateway healthy?" answered with real verdicts (pool rejections, error
ratio, own Bedrock spend vs budget) is the cheapest end-to-end validation — zero blast
radius. → Tracked: B-21 first entry.

> Superseded within this round: an earlier draft of §F framed round 4 as a native
> proactive detection loop (poller, alert budgets, shadow watching). The user corrected
> the direction the same day, before commit: **that is another product**. Recorded here
> so no future session re-proposes proactive watching as squad scope.

## D. Reading order for future sessions

1. This file (the why) → 2. `specs/BACKLOG.md` (the what, live) → 3. ROADMAP work order
(the when). When an item ships, update BACKLOG — never edit this record.
