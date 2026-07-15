# Behavior & Baselines — teaching the squad WHAT it is looking at

**Status**: living guide (v1, 2026-07-04)
**Scope decision (D-03, corrected 2026-07-04)**: the squad is **reactive by design** —
it acts ONLY when triggered (a user question or an incoming alert). Proactive
watching/alerting is a **separate product** (`staffops-anomaly-detection`'s job; its
integration remains optional). What the squad MUST have is **on-trigger comprehension**:
when it receives something, it minimally understands what that thing is and whether the
behavior it observes is normal — instead of handing raw numbers to the LLM and hoping.
**Companions**: `specs/EVIDENCE-MODEL.md` (verdicts map to its signals),
`specs/PRODUCT-REVIEW-2026-07.md` §F.

---

## 0. The problem this solves (why raw data ≠ understanding)

Today, asked "is checkout slow?", the flow fetches p99 = 840ms and the LLM guesses
whether that is bad. It has no idea what 840ms means FOR THIS SERVICE at THIS hour.
The assertive version computes, in code, at request time:

> p99 now = 840ms · baseline (same hour-of-week, 3w median) = 210ms ± 40 (MAD) ·
> **deviation ≈ 15× MAD → ABNORMAL, started ~14:05**

…and hands the LLM that **verdict** as context. The model then explains and
investigates instead of guessing. Same LLM, radically more assertive answer —
because the judgment was made by statistics, not by vibes.

Three moments consume this, all reactive:

| Trigger | What comprehension adds |
|---------|------------------------|
| User question about an entity | entity resolved + per-signal verdicts injected as context |
| Alert at `/alerts/incoming` | alert parsed → entity + signal + "still abnormal now?" verdict BEFORE investigating |
| RCA evidence collection | collected signals arrive as verdicts (EVIDENCE-MODEL strength/layer pre-filled) — the correlator gets judged evidence, not prose |

---

## 1. Principles

1. **Reactive only.** Nothing here polls, watches or notifies. Baselines are computed
   on demand (window queries at trigger time) or read from a cache warmed by usage.
2. **Judgments are CODE; the LLM interprets judgments.** Robust statistics decide
   "normal vs abnormal"; the model explains, correlates, recommends. Never the reverse.
3. **No signal without a question.** Every signal in an entity's profile answers a
   named failure hypothesis ("if X deviates, Y breaks for Z"). Can't name it → drop it.
4. **Every verdict is evidence.** A computed verdict maps to an EVIDENCE-MODEL signal
   id (layer + strength) so comprehension and RCA speak one language.
5. **Honesty when blind.** No baseline available (new entity, missing datasource) →
   the verdict is explicitly `no-baseline` and the answer says so (pairs with
   calibrated honesty, PR-28) — never silently pretend to know.

---

## 2. The worksheet — defining comprehension for an entity class

> Fill ONE per entity class. Answering these questions IS the "how to define" process.

| # | Question | What a good answer looks like |
|---|----------|-------------------------------|
| W1 | **Entity**: what is it, and how is it RESOLVED from a mention? | "checkout" → k8s workload `checkout` (ns `shop`), Prometheus `service_name="checkout"`, cost tag `svc:checkout` — the alias map is the point |
| W2 | **Why**: which failure hurts whom? | Named failure modes + blast radius; defines which signals matter |
| W3 | **Criticality tier** | tier-1/2/3 — controls investigation depth defaults, not watching |
| W4 | **Behavior signals**: which 3–6 signals define its health? | From the catalog (§3), each tied to a W2 failure mode |
| W5 | **Baseline method** per signal | From the decision tree (§4) |
| W6 | **Verdict semantics**: what deviation counts as abnormal? | e.g. "robust-z ≥3 sustained ≥5min = abnormal; 2–3 = borderline" |
| W7 | **Evidence mapping** | EVIDENCE-MODEL id + layer per signal (verdict → Evidence) |
| W8 | **Blind spots** | Signals we CANNOT compute today (missing datasource) — declared, so answers can say "unverified" honestly |

---

## 3. Signal catalog by entity class (starter menu — WHY column mandatory)

> `EM:` = EVIDENCE-MODEL id the verdict maps to. Pick 3–6 per entity, mapped to W2.

**Services / APIs (RED + saturation)** — request rate (traffic collapse/surge — C5) ·
error ratio (user-visible damage — I1) · latency p99 (degradation before errors — I2) ·
saturation: throttling/conn pools (the "about to break" leading signal — M7)

**Workloads / pods** — restart delta (crash loop — I4) · OOMKills (leak or
under-provision — M5) · memory working-set trend (leak BEFORE the OOM, Track B — M6) ·
replicas available vs desired (scheduling failure — M12)

**Nodes / infra (USE)** — memory/disk pressure (multi-workload blast radius — M13) ·
NotReady / churn (infra instability — C3) · disk % (slow-burn with a deadline — add to EM)

**Cost (FinOps)** — daily spend by tag vs same-weekday baseline (leak while cheap) ·
new top-N mover (silent architectural change) · the squad's own Bedrock spend
(self-awareness — `aigent.cost.estimated`) — cost ids: add to EM

**Platform hygiene (deadline-type)** — cert days-to-expiry (100%-preventable outage) ·
ExternalSecret/cert-manager sync failures (silent rot — C7) · quota headroom (add to EM)

**Queues / async** — depth trend + consumer lag age (stall, Track B — signature #14)

---

## 4. Baseline method — decision tree per signal

```
Hard known limit exists (disk 90%, cert <15d, quota, $ budget)?
 ├─ YES → STATIC threshold. Explainable; review quarterly.
 └─ NO → Signal roughly stationary day-to-day?
     ├─ YES → ROBUST-Z: rolling median + MAD (7d window, computed from a
     │        window query at request time); abnormal at |z| ≥ 3 sustained.
     │        Median/MAD, never mean/stddev — outliers poison the mean.
     └─ NO → Cyclic (hour-of-day / day-of-week)?
         ├─ YES → SEASONAL-NAIVE: compare to same hour-of-week median
         │        (3–4 weeks); abnormal at k× that slot's MAD.
         └─ NO (trend/counter) → DERIVATIVE: rate-of-change vs its own
                  robust baseline (catches leaks/queue growth).
```

Guardrails: **minimum history** (no verdict from <7d of data → `no-baseline`, W8
honesty) · **sustain requirement** (M of N samples; single spikes are `borderline`,
not `abnormal`) · **incident exclusion** when computing baselines over windows that
contain known incidents (KB lookup) · **simple first, ML never by default** — the four
methods cover ~90% at $0; a signal that defeats all four is the ONLY justification to
import ML (that is where the optional anomaly-detection integration/patterns earn a
place — as a baseline SOURCE, not as the trigger).

Cost note: verdicts are window queries + arithmetic — pennies of datasource load,
zero LLM. Optionally cache computed baselines (Redis, TTL hours) keyed by
entity+signal+slot so repeat questions don't recompute.

---

## 5. Verdict contract (what the LLM receives instead of raw numbers)

```json
{
  "entity": "svc:checkout",
  "signal": "latency_p99",
  "now": "840ms",
  "baseline": {"method": "seasonal-naive", "expected": "210ms", "mad": "40ms", "window": "3w same-hour"},
  "verdict": "abnormal",          // normal | borderline | abnormal | no-baseline
  "deviation": "15x MAD",
  "since": "2026-07-04T14:05Z",   // when deviation started (window scan)
  "evidence": {"em_id": "I2", "layer": "impact", "strength": "forte"}
}
```

- Injected into `<infra_data>` alongside (not replacing) a truncated raw sample.
- `no-baseline` verdicts MUST surface in the answer ("could not assess normality of X").
- In investigations, verdicts enter `Evidence[]` directly — strength/layer pre-filled.

## 6. Measuring comprehension quality (reactive metrics — no alert budgets)

| Metric | Definition | Target |
|--------|-----------|--------|
| Verdict precision | operator feedback (👍/👎, B-03) on answers that leaned on a verdict | ≥ 80% |
| Verdict coverage | % of entity-questions answered WITH a verdict (vs raw-only) | grows per rollout step |
| Honesty rate | % of blind spots correctly declared (`no-baseline` surfaced) | 100% — eval-checked (spec 35) |
| Baseline freshness | age of cached baseline at use | < 24h |
| Cost per verdict | datasource + compute per verdict | ~$0 (no LLM) |

These join spec 35's golden sets (questions whose expected answer includes the correct
verdict) and TRIGGERS.md (spec 33) for trend review.

## 7. Rollout (reactive validation — no shadow watching)

```
1. Fill the worksheet for ONE entity class; wire entity resolution (W1 alias map)
2. Implement verdicts for its 3–6 signals; add golden questions with KNOWN verdicts
   (spec 35) — "is X normal right now?" must answer correctly against fixtures
3. Enable verdict injection for that class; measure precision via feedback
4. Extend /alerts/incoming parsing: alert → entity + signal + current verdict
   BEFORE investigation starts
5. Next entity class. Entity #1 = the squad itself (ask it "is the gateway healthy?"
   — cheapest end-to-end validation, zero blast radius)
```

## Appendix — proactive reuse (explicitly OUT of this product)

If a proactive watcher is ever wanted, it is a SEPARATE product/process that CONSUMES
these same worksheets/baselines on a schedule (that is `staffops-anomaly-detection`'s
territory — or its enriched alerts simply arrive at `/alerts/incoming` like any other).
Nothing in the squad polls, watches or pages. This appendix exists so the boundary is
never blurred again.
