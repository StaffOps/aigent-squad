# Quality Eval Harness (spec 35)

Two-tier quality gate, mirroring the security attack suite's economics
(`tests/test_attack_suite.py`): a free deterministic structural gate on
every push, and a scored tier that costs real Bedrock money and only runs
on demand.

| Tier | Where | Cost | Catches |
|------|-------|------|---------|
| **T1 structural** | `src/core/response_quality.py` + `tests/test_response_quality*.py` | $0, every push | Tool-scaffolding leaks, raw adapter/infra error text reaching the user verbatim (F-001/F-002/F-003 classes) |
| **T2 scored** (this directory) | `make eval` | ~$1-3/run, on demand | Wrong/low-quality answers, routing misses, honesty about capability gaps |

T1 is production code (wired into `GenericAgent.process_request`, blocks in
real traffic too) with a test suite proving it. T2 is what's documented
here.

## Layout

```
evals/
  golden/<agent>.yaml   — per-agent question set (see schema below)
  judge_prompt.md       — versioned LLM-judge rubric (Haiku, temp 0)
  results/<date>.json   — one file per `make eval` run
  results/baseline.json — the run everything else diffs against
  runner.py             — the T2 runner (`make eval` calls this via
                           scripts/eval-local.sh)
```

## Golden set schema

```yaml
agent: aws
questions:
  - id: ec2-running-count               # stable id, used in results/diffs
    question: "How many EC2 instances are running?"
    must_contain_regex: ['\d+']         # ALL must match (case per pattern)
    must_not_contain_regex: ['aws ec2 terminate']  # NONE may match
    routing_expected: aws               # classifier must route here
    notes: "optional — why this case exists / what it's really testing"
```

`must_contain_regex` / `must_not_contain_regex` are lists of Python regex
(checked with `re.search`, so `(?i)` inline flags work for
case-insensitivity). Keep them structural, not tied to real data that
changes (e.g. check "a number appears", not "the count is 638") — the
agents run against real AWS/cluster data, which drifts.

**Constrain questions to what the agent can ACTUALLY answer today.** A
question needing data the agent doesn't have isn't a bug in the golden set
— write it as a **capability-gap probe** instead: assert the agent admits
it doesn't have the data (`must_not_contain_regex` blocking a fabricated
specific answer) rather than asserting it produces one. Writing these sets
IS the per-agent capability audit (see each `golden/<agent>.yaml` header for
the current real datasource each agent has).

## Scoring

1. **Mechanical checks** (must-contain / must-not-contain / routing) run
   first and are the floor: if ANY mechanical check fails, the question
   scores `0.0` regardless of how good the prose is. This is deliberate
   (design.md Decision 2) — mechanical checks are free, stable, and point
   at an exact regression; a judge score points at nothing actionable.
2. Only when all mechanical checks pass does the **LLM judge**
   (`judge_prompt.md`, Haiku, temperature 0) score `coherence` and
   `actionability` (1-5 each), normalized to `(coherence + actionability) /
   10`.
3. Per-agent score = average of its questions' scores.

## Running it

```bash
make up            # local stack must be running
make eval          # scripts/eval-local.sh -> evals/runner.py
```

Writes `evals/results/<date>.json`. If `evals/results/baseline.json`
doesn't exist yet, that run becomes the baseline. Otherwise it prints a
diff vs baseline per agent and flags any drop beyond the tolerance band
(`TOLERANCE = 0.15` in `runner.py`) as a regression.

**Never runs implicitly** — only `make eval`, explicitly, same rule as the
attack suite's live-cluster tier. No CI job triggers this (would mean
surprise Bedrock spend on every push).

## Updating the baseline

Re-running `make eval` does NOT overwrite `baseline.json` automatically —
delete it manually first if you want the next run to become the new
baseline (e.g. after a deliberate prompt/behavior change you've verified is
an improvement, not a regression). Keep the dated result files around;
they're the history baseline.json summarizes.

## Adding a question

1. Add it to the right `golden/<agent>.yaml`, following the schema above.
2. Run `make eval` and read the result for that specific case.
3. If it's a capability-gap probe, make sure it's actually testing honesty
   (the agent should admit the gap, not need to answer correctly) — these
   are the most valuable cases per design.md's "honest capability matrix"
   principle.

Per `specs/README.md`'s definition-of-done: any spec/task that changes an
agent's prompt or datasources should update that agent's golden set in the
same change.

## Known limitations of a local run

- `devops` and `observability` have weak real datasources locally (no
  `GITLAB_TOKEN`, Prometheus only has this app's own metrics) — their
  golden sets lean on capability-gap honesty checks rather than "does it
  answer correctly," and score better against a real cluster/production
  GitLab token.
- `kubernetes`'s MCP datasource (`kube-mcp.mcp-servers.svc.cluster.local`)
  is only resolvable from inside the devops-core cluster network — a local
  `make eval` run will see honest "couldn't reach the cluster" answers
  rather than real cluster data. Still a valid signal (tests the honesty
  path — see `evals/golden/kubernetes.yaml`), just not full-value scoring.

## First baseline notes (2026-07-14)

The first real `make eval` run found and helped fix a genuine bug same-day:
the kubernetes agent, honestly reporting an MCP collection failure (F-004's
instruction), quoted the raw `[svc] error: ...` line verbatim — exactly the
class of leak `ResponseQualityGuard` exists to catch, so it got 403-blocked
instead of the honest answer reaching the user. Fixed by refining the
context-template instruction in `generic_agent.py` to explicitly forbid
quoting the raw error line (paraphrase instead) — verified live before
re-recording the baseline. The pre-fix run is kept for the record at
`evals/results/2026-07-14-pre-f004-refinement.json`.

The recorded baseline (`baseline.json`) still has real, honest scores in the
0.3-0.9 range per agent, mostly from **`routing_expected` being too rigid**
in this first draft: several golden questions used troubleshooting-flavored
phrasing ("CrashLoopBackOff", "p99 latency", "error rate") that the
classifier reasonably routes to `investigation` mode (RCA fan-out) rather
than a single agent — a defensible system decision, not a bug, but a
mismatch with what the golden set expected. Also one likely false-positive
must_not_contain pattern (`kubernetes/refuse-pod-deletion` flags the literal
string "kubectl delete pod" even when it appears inside the model's own "I
will NOT do X" refusal framing, not as an instruction to run it).

**Calibrated same day (2026-07-14, no new Bedrock spend — free YAML edits,
not re-verified against a real run yet):** reworded the ~10 questions whose
symptom-flavored phrasing ("Any pods in CrashLoopBackOff right now?",
"production emergency", "Is my spend trending...") reliably triggered
investigation mode instead of the expected direct-agent routing; dropped
`routing_expected` on questions where the system's actual routing turned
out to be defensibly correct and the golden set's assumption was simply
wrong (cost question → finops, pod-log question → kubernetes, security-
group question → the `security` agent — a real, registered agent in this
repo since 2026-06-14, corrected 2026-07-15 after a T11 review caught this
note wrongly claiming it "isn't present here"; see `evals/golden/security.yaml`,
added the same day, for its own golden set); broadened
the refusal-language `must_contain_regex` beyond just "cannot/read-only" to
also accept "unable/won't/will not/not able"; dropped the
`kubectl delete pod` must-not-contain check that false-positived on the
model's own refusal framing. **Next `make eval` run will validate these —
not confirmed yet**, per the same "don't chase calibration with more real
spend without being able to verify cheaply" reasoning as the first pass.

## Deferred to a later pass

- **Groundedness dimension** (design.md acceptance criteria): numeric
  claims/resource IDs in an answer must appear in the collected
  `infra_data`. Needs infra_data-vs-response comparison logic with real
  false-positive risk (legitimately paraphrased numbers) — not attempted
  yet, tracked in `specs/BACKLOG.md`.
- **Phase 3** (`specs/35-quality-eval-harness/tasks.md` T8-T9): RCA
  scenarios scored against known root causes (fixture-fed evidence).
