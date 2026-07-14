# Handoff — sessions 2026-06-16 → 2026-07-14

Estado para retomar. O que foi feito, o que ficou pendente, e próximos
passos priorizados.

---

## Done — session 2026-07-13 (follow-up queue: F-001, F-002, spec-14 E2 — uncommitted)

**Worked the 3-item follow-up queue left after the 0.4.0 gate closed** (release
itself deferred — user chose to clear the queue first). All three fixed,
tested (`make test-one` + full `make test` 696 passed / 94.25% cov), linted
clean. Nothing committed yet — pending user review/approval.

- **F-001 (aws `<use_mcp_tool>` XML leak) — root cause found + fixed.**
  `agents/aws/agent.yaml` declares no `type: mcp` datasource (only `boto3`) —
  no `aws-mcp-server`/`cost-mcp-server` is actually deployed (config.py's
  `aws-mcp` entry is an unprovisioned placeholder, unlike kubernetes' real
  `k8s-mcp` → `bdc.app.br`). `prompt.md` nonetheless told the model to "use
  MCP servers directly", so it hallucinated Cline/Roo-style `<use_mcp_tool>`
  XML (Bedrock tool-use API is never wired — `bedrock.py` sets no `tools`).
  **Fix:** removed the "Available MCPs" section + "query via MCP" line from
  `agents/aws/prompt.md`. spec 35 T1 regression fixture still pending (harness
  not built).
- **F-002 (finops↔Athena AccessDenied) — fixed per the recorded decision.**
  Dropped the `athena` datasource from `agents/finops/agent.yaml` (kept
  `boto3 ce`, which already returns real spend). Updated
  `docs/site/agents/overview.md`, `specs/BACKLOG.md`, `specs/ROADMAP.md`
  (dormant note + backlog table) to reflect the resolution. Re-enable path
  documented: `enable_athena_finops=true` + CUR vars once a real target exists.
- **Spec-14 Finding E2 (budget/history session decoupling) — CLOSED.**
  Root cause: RCA evidence-collection fan-out (`run_investigation`) booked
  Bedrock spend under `session_id=f"{session_id}-inv-{state.id[:8]}"` — a
  fresh, per-investigation-unique budget bucket `check_budget()` never reads,
  so evidence tokens practically escaped the session cap. Fix: new
  `budget_session_id` param threaded through `GenericAgent.process_request` →
  `BedrockClient.invoke`/`_invoke_sync` → `budget_tracker.record_usage`,
  decoupled from `session_id` (kept for log/audit correlation only —
  `GenericAgent.process_request` never persists history to DynamoDB itself).
  `run_investigation`'s fan-out now passes `budget_session_id=session_id` (the
  real parent session). Tests: `tests/test_spec14_e2.py` (5 cases, self-authored
  — not independent-author per the spec-14 process). Detail + status in
  `specs/14-security-hardening/tasks.md`.
- **Not done:** independent security review of the E2 fix, cluster
  re-homologation, spec 35 T1 fixtures for F-001/F-002 (harness doesn't exist
  yet), and the `0.4.0` release cut itself (still queued, per user's explicit
  choice this session).

### Also this session — LibreChat (real cluster) + 2 more live findings (F-003, F-004) + F-005 open

**Started as "enable LibreChat", ended up surfacing three more real defects**
via actual end-to-end testing against the live `devops-core` cluster (not just
local docker-compose). `kubectl`/`helm` context on this machine is already
`devops-core` — used read-only throughout (`helm list`, `helm get values`,
`kubectl logs`, `kubectl exec ... python3` for connectivity checks) plus one
read-only AWS Secrets Manager fetch (`STAFFOPS_AIGENT_SQUAD`, never printed).

- **`infra/values/`** — user pasted the real applied Helm values (`values.yaml`)
  + the helmfile environment snapshot (`var.yaml`) for the `aigent-squad`
  release. Verified byte-for-byte match against `helm get values aigent-squad
  -n staffops` (rev 15, chart 0.9.2) — confirmed accurate, not stale. Inlined
  `var.yaml`'s `aigent_squad_host` into `values.yaml` (was a `{{ .Values.* }}`
  gotmpl placeholder) and deleted `var.yaml` per instruction; its other
  context (kubeContext, namespace, real chart version vs. the helmfile's stale
  pinned `0.8.0`) preserved as a comment block.
- **LibreChat decision — real Helm addition, then walked back to local-only.**
  Explored a full custom Helm chart (found LibreChat's own official upstream
  chart + confirmed `redirect-containers-to-harbor` Kyverno policy + a live
  `traefik-internal` IngressClass would have made this easy) and wrote a plan
  — then user asked me to check `StaffOps/chaitops` first. Its `TODO.md`
  explicitly lists "Helm chart | Migração para K8s" under *deliberately
  blocked, don't work until a real trigger* — chaitops runs its own LibreChat
  via plain docker-compose only. User decided: same approach here, but even
  leaner — **LibreChat runs local (`mongo` + `librechat` only), pointed at the
  squad already running in the real cluster**, no Helm/chart work at all.
  - `infra/librechat/librechat.yaml`: fixed the stale `supervisor:8000` →
    fixed again to the real cluster's public gateway
    (`https://aigent-squad.bdc.app.br/v1`), hardcoded (LibreChat does NOT
    template `baseURL` — only `apiKey`/`headers` get `${VAR}` interpolation;
    confirmed empirically after a failed attempt, and matches chaitops's own
    hardcoded `baseURL` in its equivalent file).
  - `docker-compose.yaml`: added `mongo` + `librechat` services. `librechat`'s
    `depends_on` is `mongo` only (NOT `gateway` — the squad stays in the real
    cluster, nothing local needed beyond these two). Healthcheck fixed to
    `wget` (no `curl` in the LibreChat image). Real token pulled into
    `LIBRECHAT_AIGENT_SQUAD_API_KEY` via `aws secretsmanager get-secret-value`
    (kept separate from `INTERNAL_API_TOKEN` so an unset value here never
    breaks `make up`'s unrelated full-local-stack flow).
  - **Verified end-to-end against the real cluster**: registered a test user
    via LibreChat's REST API (manually flipped `emailVerified` in Mongo — no
    SMTP locally), `GET /api/models` confirmed LibreChat fetched the live
    `AIgent-Squad` model list from the real gateway. LibreChat's own
    `/api/ask/:endpoint` chat-send route wasn't reverse-engineered via curl
    (LibreChat internal API quirk, not a sign of anything broken) — the
    user can now just use the browser UI at `http://localhost:3080`.
  - `docs/LIBRECHAT.md` rewritten for this flow (was already stale pre-spec-31,
    fixed once, then rewritten again for the local-against-real-cluster
    default).
- **F-003 (kubernetes agent `k8s-mcp` pointed at a dead hostname) — found +
  fixed in this app repo, NOT yet live.** While testing LibreChat's
  kubernetes-agent path for real, got back a fabricated-sounding "diagnostic
  report" wrapping `[mcp:k8s-mcp] error: unhandled errors in a TaskGroup (1
  sub-exception)`. Root cause, confirmed via live `kubectl`/`kubectl exec`
  investigation: `agents/kubernetes/agent.yaml` pointed at
  `https://devops-mcp-kube-core.bdc.app.br/sse`, which now 404s (Traefik
  default cert — no matching IngressRoute; the real Ingress is
  `mcp-kube.bdc.app.br` → Service `kube-mcp` in namespace `mcp-servers`).
  **Fix:** repointed at the in-cluster Service DNS
  (`http://kube-mcp.mcp-servers.svc.cluster.local:8080/sse`) — the supervisor
  runs in the same cluster, so this also avoids the external-ingress hop
  going forward regardless of hostname churn. User then asked for "full MCP
  access" — confirmed live via `session.list_tools()` that `kube-mcp`'s own
  protocol surface has **no mutating tools at all** (only
  list/get/log/top/view — 23 tools total), and that RBAC is enforced at the
  MCP server's own ServiceAccount, not the app-level YAML allowlist — so
  widened `tools:` to the full catalog. **Not live**: the deployed cluster's
  agent configs come from a separate GitLab repo
  (`devops/aigent-squad.git`, git-sync init container), not this app repo —
  not accessible this session, needs porting (tracked, task/backlog F-003).
  Side note: the live cluster also has a 6th agent, `security`, not present
  in this app repo's `agents/` — a sync-drift signal, not investigated.
- **F-004 (adapter errors → fabricated diagnostics) — found + fixed.** Same
  discovery as F-003 exposed a *systemic* pattern: every adapter type
  (`Boto3Adapter`, `KubernetesAdapter`, `HttpAdapter`, `AthenaAdapter`,
  `McpAdapter`) returns raw `f"[svc] error: {e}"` text as if it were valid
  `infra_data` on failure (by design — one datasource failing shouldn't kill
  collection), but nothing told the model how to treat that. The elaborate
  "world-class expert" agent prompts then narrate a full invented
  troubleshooting report around it. **Fix:** one instruction added to the
  *shared* context template in `src/core/generic_agent.py` (used by every
  agent) — state collection errors plainly, never invent root cause/remediation
  for data not actually collected. Test:
  `tests/test_generic_agent.py::TestCollectionErrorHonestyInstruction`. Full
  suite re-run clean after this change: 697 passed / 94.25% cov.
- **F-005 (CLOSED) — spec-14 L5 canary false-positives on ordinary verbose
  answers.** Investigated properly this session (not deferred a third time):
  wrote throwaway repro scripts (`docker run` against the real
  `aigent-squad-supervisor` image, real AWS/Bedrock creds, real
  `Boto3Adapter` data) to run the aws agent's real pipeline and capture the
  exact raw response around each block. **Confirmed root cause**: the model
  has a learned habit of ending "thorough" technical answers with a
  "Session:"/"Trace:"/"Reference:" footer, and grabs the canary token — the
  only opaque-hex value visible in its context — to fill it. Pure
  helpfulness, zero injection or malicious intent involved. Two format
  mitigations, in order:
  1. An explicit "never repeat this value" clause placed *inside* the
     injected marker text — did nothing. The anti-prompt-injection framing
     tells the model everything inside `<infra_data>` is inert DATA, so an
     instruction embedded there is *also* just data to it — self-defeating
     by construction.
  2. Reformatted the marker (`[session-ref: ...]` → HTML-comment annotation
     `<!-- internal-telemetry-id, do not output: ... -->`) + moved the
     instruction to the *system-level* context template (`generic_agent.py`,
     outside `<infra_data>`) — 0/14 leaks in the final, correctly-tested live
     trials.
  - **Methodology correction, logged for honesty**: mid-session "leak rate"
    figures (25%→13%) reported to the user were **retracted** — those repro
    runs used a stale `docker run` image that predated the code edits under
    test (forgot to mount the live `src/`), so they weren't measuring the fix
    at all, just re-sampling the original bug twice. Caught this, fixed the
    mount, re-ran (0/14 clean) before closing the finding. Flagged explicitly
    to the user rather than quietly correcting it.
  - **Policy decision — asked the user directly, they chose B**: redact-and-
    continue instead of hard-block. `CanaryGuard.detect()` now returns the
    response with any leaked token replaced by `[redacted]` (audit log still
    fires) instead of raising `GuardrailBlockedError`. This is a deliberate,
    informed deviation from spec-14's fail-closed default for security
    layers — for L5 specifically, not the others (Guardrail/InputScanner/
    OutputFilter stay fail-closed). Rationale: the canary token is single-use
    and worthless once redacted, so denying a real answer on a benign false
    positive cost more than it protected.
  - Updated `AGENTS.md` invariant #6 and `docs/SECURITY.md` §S4 (L5 row +
    "Fail-closed" section) to document the exception.
  - Tests rewritten for the new contract: `tests/test_canary.py` (most of
    `TestDetectLeak`/`TestFuzzyDetection`/etc.), `tests/test_canary_output_
    integration.py::TestIntegrationCanaryLeak`, new
    `tests/test_generic_agent.py::TestCanaryMarkerNonRepetitionInstruction`.
    Full suite: 700 passed / 94.25% cov, lint clean.
  - Detail: `specs/BACKLOG.md` F-005, `specs/14-security-hardening/tasks.md`.
- All five findings recorded in `specs/BACKLOG.md` (F-001 through F-005).

**Committed** (3 commits on `dev`, not pushed): `d96d98a` (security/quality
fixes: E2 + F-001..F-005), `e64a660` (LibreChat local setup + infra/values),
`04539d2` (HANDOFF summary).

### Also this session (2026-07-14) — spec 35 Phase 1 + prompt cleanup (F-006)

User asked to "accumulate more improvements" before cutting `0.4.0` (still
deliberately deferred) rather than push/release immediately. Picked the two
candidates offered: the quality structural gate (spec 35, highest ROI —
would have caught F-001/F-002/F-003 automatically) and cleaning up the
verbose agent prompts (a root-cause contributor to F-001/F-005).

- **Spec 35 Phase 1 (T1 structural gate) — DONE.** `specs/35-quality-eval-
  harness/` already had a full `requirements.md`/`design.md`/`tasks.md`
  (written 2026-07-04, nothing implemented). Design decision resolved during
  planning: `design.md` says T1 works via "a regex over the final response,"
  which implies real production code, not just tests — so a request scoped
  to "add tests" would have missed the point. Shipped:
  - `src/core/response_quality.py` (`ResponseQualityGuard`) — new L4-sibling
    guard, same shape as `OutputFilter`. Scans every response for
    tool-scaffolding tags (`<use_mcp_tool>`, `<tool_call>`,
    `<function_calls>`, `<invoke>`) and raw adapter/infra error signatures
    (our own `[svc] error:`/`[mcp:...] error:` prefix, Python tracebacks,
    `botocore.exceptions.*`, boto3's `An error occurred (...) when calling`,
    anyio's `unhandled errors in a TaskGroup`). **Block, not redact** —
    unlike F-005's canary decision, neither defect class is ever legitimate
    content, so there's no benign-false-positive case to preserve
    availability for. Wired into `GenericAgent.process_request` (same slot
    as `OutputFilter`). Config: `response_quality_enabled` (default `true`).
  - Metric: `aigent.quality.violations` (labels `agent_id`, `category`),
    documented in `docs/METRICS.md`.
  - Tests: `tests/test_response_quality.py` (21 unit tests, guard behavior,
    100% cov) + `tests/test_response_quality_regression.py` (6 tests — the
    actual regression proof: mocked-Bedrock `GenericAgent.process_request`
    reproducing the EXACT live-observed text for F-001, F-002, F-003, plus
    a false-positive sanity check that a legitimate "your policy denies
    access because..." advisory answer is NOT flagged). Full suite: 727
    passed, 94.35% cov, lint clean.
  - `specs/35-quality-eval-harness/tasks.md` T1-T3 marked done; groundedness
    checking (needs infra_data-vs-response comparison, more false-positive
    risk) explicitly deferred to Phase 2, not silently dropped. Phase 2/3
    (golden sets, LLM judge, RCA scenario scoring) untouched — real Bedrock
    cost, out of scope for this pass.
- **Prompt cleanup (F-006) — DONE.** Trimmed `agents/{aws,finops,kubernetes}/
  prompt.md`: removed the "15+ years / world-class / certified expert"
  framing and the emoji-severity-graded multi-section example-response
  templates, kept all substantive content (read-only rules, domain
  knowledge, collaboration hints, behavior rules, one positive/negative
  example each). Added an explicit "don't add a session/trace footer" line
  (direct F-005 callback). finops also stopped overclaiming Kubecost
  capability it doesn't have since F-002 dropped that datasource — the
  prompt now says plainly when it lacks the data for something (e.g.
  historical trend) instead of fabricating an answer. Also fixed a stray
  Portuguese line in the old aws prompt ("Você domina COMPLETAMENTE") and an
  orphaned/duplicated fragment in the old kubernetes prompt (copy-paste
  artifact, unrelated content stitched in after the "Mission" section) —
  both were pre-existing bugs unrelated to the hype-trimming goal, fixed
  while in the file.
  - **Live-verified against real Bedrock** (same `docker run` harness used
    for F-005, mounting live `src/`+`agents/`, real AWS/Bedrock creds):
    responses on identical queries dropped from 1200-1550 chars pre-cleanup
    to 89-379 chars post-cleanup — 3-4x shorter, same factual content, more
    honest about data limits (finops: "I don't have historical comparison
    data... so I cannot show you a trend" instead of inventing one). 0/8
    canary redactions in a follow-up batch (down from a real non-zero rate
    pre-cleanup) — the calmer prompt style measurably reduces the F-005
    footer-fabrication tendency, though this wasn't the primary goal and
    isn't claimed as a full fix (the redact-and-continue mechanism is the
    actual guarantee).
- Recorded as F-006 in `specs/BACKLOG.md`; F-001/F-002 status updated to
  ✅ CLOSED (their spec-35 T1 regression fixtures now exist).

### Next
1. Push the 4 commits on `dev` (3 from earlier + this session's spec-35/F-006
   work, not yet committed as of this HANDOFF write) and confirm CI green.
2. Port the F-003 kube-mcp fix to the live git-sync repo
   (`devops/aigent-squad.git` — not accessible this session).
3. Cut `0.4.0` (still queued, still deliberately deferred — release skill,
   dev→main→tag→chart→cluster — cut when the user decides enough has
   accumulated).
4. Independent security review of E2 and F-005's policy change (both touch
   spec-14's fail-closed invariant) before calling either cluster-verified.
5. Spec 35 Phase 2 (golden sets + LLM judge + RCA scenario scoring) — real
   Bedrock cost, `make eval` — natural next candidate if more "accumulate
   improvements" rounds continue before `0.4.0`.

---

## Done — session 2026-07-11 (spec 14 Phase 6: entry-point findings A/B/C/D CLOSED)

**The 0.4.0 gate work.** Pipeline dev→test→security completed for the four
homologation findings. Detail in `specs/14-security-hardening/tasks.md` +
`design.md` (new "Phase 6 — Entry-point hardening" section).

### Shipped (uncommitted, on `dev` working tree)
- **Fix A+C** — `classifier.classify` accepts + forwards `user_id`/`session_id`
  to `bedrock.invoke`: classifier-stage guardrail blocks attributable, Haiku
  classifier tokens now budget-counted. Defaults preserved.
- **Fix B** — `InputScanner` at `supervisor.process_request` (after budget
  check, before force_agent/investigate/classify; `agent_id="supervisor"`).
  Normalized text replaces `user_input` downstream incl. saved history.
- **Fix D** — oversized `ValueError` removed from `generic_agent`;
  `scanner:oversized` fail-closed 403 is the single enforcement point.
- **Tests** — 11 new (`tests/test_spec14_entrypoint.py`, independent author) +
  `test_generic_agent.py` updated to the new oversized contract.
- **Security review** — independent, **APPROVE-WITH-NITS**; A/B/C/D confirmed
  CLOSED with code-path evidence.
- **Docs** — `docs/SECURITY.md` §S4 "Known gap" → "Entry-point hardening
  (closed)"; spec 14 tasks.md findings marked CLOSED + review record.

### New follow-up findings from the review (OPEN, in spec 14 tasks.md)
- **E (MEDIUM)** — synthesizer + `_synthesize_rca` invoke Bedrock with empty
  `session_id` → unattributable OUTPUT blocks + Sonnet synthesis tokens escape
  the budget; investigation books evidence to a `-inv-` bucket `check_budget`
  never reads.
- **F (MEDIUM)** — `/alerts/incoming` → `run_investigation` bypasses entry L2;
  raw alert-derived symptom reaches RCA synthesis (only raw L1). Fix: scan the
  symptom at the top of `run_investigation`.
- Decide: fold E/F into the `0.4.0` gate or ship 0.4.0 with A/B/D closed and
  track E/F for 0.4.x.

### Also this session
- Pushed the 4 pending commits (`9bda015`→`63f1d5c`) to GitHub `dev` — CI green
  (Test + SAST). The GitLab overlay commit `c1c9195` push status: still pending
  (not in this repo).
- Full suite before fixes: 674 passed / 93.91% cov. Re-run with the 11 new
  tests: pending at handoff-write time (background).

### Next
1. ✅ Committed + pushed (`7a37808` → GitHub `dev`, CI green: Test 1m30s + SAST).
2. ✅ Re-homologated in devops-core (2026-07-11). Rebuilt multi-arch image with the
   Phase-6 fixes → Harbor `labs/aigent-squad:0.3.0-dev` digest `sha256:657d9a35`;
   rolled out gateway+supervisor (2/2). Attack battery via `/query`: **8/8 vectors
   block, all attributable** (real session_id) — homoglyph/zero-width now blocked
   pre-route (folded at supervisor entry → classifier guardrail), oversized now 403
   (`scanner:oversized`). A/B/C/D confirmed CLOSED in-cluster. Evidence table in
   `specs/14-security-hardening/tasks.md` ("Cluster re-homologation"). Repro script:
   scratchpad `homolog.py`.
3. ✅ **Findings E1 + F CLOSED** (2026-07-11, uncommitted on `dev` working tree).
   - **F** — `_scanner.scan(symptom, agent_id="investigation")` at the top of
     `run_investigation` (`investigation.py`): single choke point covering
     `/alerts/incoming` (was un-normalized → only raw L1) + the investigate path
     (idempotent re-scan) + future callers. Fail-closed → 403; normalized symptom
     propagates. The `/alerts` batch handler's `except Exception` turns a block into
     "no RCA for that alert" (audit already emitted) — correct fail-closed for a
     batch webhook.
   - **E1** — `synthesizer.synthesize` + `_synthesize_rca` now forward
     `agent_id`/`user_id`/`session_id` to `bedrock.invoke` (attribution +
     synthesis-token budget). Call sites `agent.py:_fan_out` + `run_investigation`
     pass the real session.
   - **E2** carved to **0.4.1** (evidence fan-out books to the `-inv-` budget bucket;
     needs decoupling budget-session from history-session — not mechanical). Detail
     in `specs/14-security-hardening/tasks.md`.
   - Tests: `tests/test_spec14_ef.py` (6, independent author). Suite **691 passed /
     94.24%**, lint clean. Self-reviewed for security; **independent security review
     + cluster re-homologation of E1/F still pending** before the tag.
4. ✅ **E1/F committed + pushed + cluster-homologated** (2026-07-12).
   - Commit `be32491` → GitHub `dev`, CI green (Test + SAST).
   - Image rebuilt with E1/F → Harbor `labs/aigent-squad:0.3.0-dev` digest
     `sha256:e3e5948`; rolled out gateway+supervisor (2/2).
   - Re-homologation: base 8-vector battery still 8/8 (no regression). `/alerts/incoming`
     with base64+homoglyph symptoms → `input_scanner_block agent=investigation` (base64) +
     folded-then-guardrail-blocked (homoglyph); webhook `HTTP 200 triggered:0`, no RCA,
     all audited. **Fix F proven: the alert path now has entry-stage L2.** Evidence in
     `specs/14-security-hardening/tasks.md`.
5. **0.4.0 gate FULLY MET** — A/B/C/D + E1/F closed and cluster-homologated; E2 carved to
   0.4.1. **Next: cut `0.4.0`** (via the `release` skill — dev→main→tag→chart→cluster;
   needs go-ahead). Then: aws-agent `<use_mcp_tool>` XML leak, finops↔Athena, finding E2.

> Local-access note: this machine's `aws` cli is 2.6.1 (2022) — emits `v1alpha1`
> ExecCredential that kubectl 1.34 rejects. A shim in scratchpad rewrites it to
> `v1beta1`; cluster access also required mapping the SSO admin role (done by user
> mid-session). Upgrading the aws cli removes the need for the shim.

---

## Done — session 2026-07-02 → 2026-07-03 (spec 14 complete + cluster homologation)

**Milestone: spec 14 (anti-prompt-injection defense-in-depth) is functionally
complete (Phases 1–5) and homologated in the real cluster.** Plus Redis
StatefulSet, ElastiCache validate-and-destroy, spec 11, and budget TOCTOU.

### Shipped + committed + CI green (on `dev`)
- **Spec 11** — model tiering (Haiku classifier / Sonnet agents) + prompt caching
  + token budget; Haiku 4.5 pricing corrected ($1/$5/$0.10).
- **Budget TOCTOU (T19d)** — `check_budget` now atomic via Lua `EVAL`
  check-and-reserve; `fakeredis[lua]` added to CI.
- **Redis in-cluster** — Deployment → **StatefulSet + PVC** (chart 0.9.2).
  Homologated: AOF persistence survives pod delete (keys + budget counter
  reloaded from PVC). Running as `aigent-squad-redis-0`.
- **ElastiCache** — module applied (Valkey 8.0.1, tested PING/SET/GET from
  cluster) → **destroyed** (zero cost, confirmed via AWS). Module stays
  validate-only in `infra/terraform/elasticache/`.
- **Spec 14 Phase 2** — canary tokens (inject into infra_data + fuzzy detect) +
  output filter (PII/secret/canary, Luhn on credit cards).
- **Spec 14 Phase 3** (`59386e8`) — `InputScanner` L2 (NFKC + zero-width/RTL-bidi
  strip + homoglyph fold + cheap heuristics), wired in `generic_agent`. Task 9
  reused the spec-31 `AdmissionGuard` (rate+budget). 88 tests, 100% cov.
- **Spec 14 Phase 4** (`c7b7b61`) — `tests/test_attack_suite.py` deterministic CI
  gate: 5 languages × obfuscations, 134 pass + 19 xfail-by-design. L3 reviewed
  (no change — sound). RTL/bidi strip added (security LOW-1).
- **Spec 14 Phase 5** (`fea17d2`) — `docs/SECURITY.md` §S4 rewritten (L1–L6 +
  STRIDE + fail-closed/open); `READ_ONLY_POLICY.md` cross-ref; ROADMAP updated.

### Cluster homologation (0.3.0-dev, digest `fbe381fb`, devops-core)
- Rebuilt image with InputScanner; rollout of supervisor + gateway (2/2 each).
- **Tag mismatch fixed**: chart appVersion is now the stable `0.3.0`, but local
  dev runs `0.3.0-dev` on Harbor → overlay pinned `tag: 0.3.0-dev` for
  gateway+supervisor (committed in `k8s-setup`, `c1c9195`, local only).
- Attack battery via `/query` — confirmed real flow: `/query` → supervisor →
  `classifier.classify` (raw, L1 only) → if routed, `generic_agent` (L2+L1).
  base64→L2 block ✅; homoglyph/zero-width→routed, caught at worker L1 post-L2 ✅
  (proves L2 value); fullwidth/plain→classifier L1 ✅; repeated-char→L2 ✅.

### 4 OPEN findings from homologation (deferred — security-critical path)
Documented in `specs/14-security-hardening/tasks.md` (attribution table + detail):
- **A (MEDIUM)** — `classifier.classify` drops `user_id`/`session_id` → classifier
  guardrail blocks log empty session (unattributable, breaks audit invariant).
- **B (MEDIUM)** — homoglyph evades the classifier L1 (routed through, only caught
  at worker after L2 folded). The supervisor-entry L2 gap, empirically proven.
- **C (LOW)** — same root as A: classifier invoke tokens not counted vs budget
  (`bedrock.py:165` skips on empty session).
- **D (MEDIUM)** — oversized input raises `ValueError` at `generic_agent.py:52`
  BEFORE the scanner → not a 403, degrades to HTTP 200 fallback; `scanner:oversized`
  is dead code.
- Proposed fixes: A+C (propagate ids), B (wire InputScanner at supervisor entry),
  D (drop redundant ValueError). Pipeline dev→test→security when picked up.
- **Side observation (not spec-14)**: aws agent echoes raw `<use_mcp_tool>` XML in
  the response instead of executing — track separately.

### NOT pushed yet
Two local commits await push approval:
- app `fea17d2` (docs/specs, → `dev` on GitHub, will run CI)
- overlay `c1c9195` (→ `main` on GitLab)
The `k8s-setup` `staffops/anomaly-detection/values.yaml.gotmpl` shows as modified
but is **not mine** (pre-existing drift) — left untouched.

---

## Done — session 2026-07-01 (spec 31/29 cluster-validated + 0.3.0-dev)

**The squad is running in a real cluster (devops-core) end-to-end.** First time
the code left dry-run/docker-compose. Everything below was validated with real
queries hitting Bedrock + real AWS inventory via IRSA.

### Deployed to devops-core (namespace `staffops`, inProcess topology)
- **Helmfile install** under `02-KUBE/00-CONFIG/k8s-setup/staffops/` (sibling of
  the other add-ons): two-doc `helmfile.yaml.gotmpl` (environments + releases) +
  `aigent-squad/values.yaml.gotmpl`. Follows the `dependency-track` pattern.
- **Image**: built locally (multi-arch amd64+arm64) with the gateway, pushed to
  Harbor `harbor.bigdatacorp.com.br/labs/aigent-squad:0.3.0-dev`. The overlay
  sets `global.image.registry` + `repository` with **no per-service tag** — the
  version comes from `Chart.appVersion` (0.3.0-dev).
- **Terraform applied** (12 resources): IRSA role `aigent-squad-irsa` (Bedrock
  invoke + ApplyGuardrail + DynamoDB + read-only inventory), DynamoDB
  `agent-sessions`, Bedrock Guardrail (`w11piaof9jp1` v1, PROMPT_ATTACK MEDIUM),
  2× Bedrock VPC endpoints, 1 Application Inference Profile (cost attribution).
- **Secrets**: `STAFFOPS_AIGENT_SQUAD` in Secrets Manager (INTERNAL_API_TOKEN +
  SUPERVISOR_INTERNAL_TOKEN) via ExternalSecret → ClusterSecretStore `aws`.
- **Routing**: Istio Gateway API (HTTPRoute on `istio-dvps-internal`, listener
  `https-bdc-app-br`) — TLS terminated at the gateway (Let's Encrypt, verified).
  The chart supports `routing.type: ingress|gatewayapi` in the same values.
- **agentsSource**: validated BOTH `configmap` (inline) and `git` (initContainer
  clones the GitLab agents repo `.../devops/aigent-squad.git`, tokenSecret).

### Fixes found while validating in-cluster
Chart (helm-charts, bumped to **0.9.0**, first cluster-validated release):
- ExternalSecret `v1beta1` → `v1` (ESO ≥0.10 dropped v1beta1).
- In-cluster Redis: disable persistence + emptyDir `/data` (readOnly rootfs
  tripped `stop-writes-on-bgsave-error`).
- git-sync clones into `/agents/.repo` (was `/tmp/repo` — readOnly rootfs).
- `agents[]` default config updated to the spec-22 schema (domain/capabilities/
  datasources objects) — old string list crashed the supervisor (Pydantic).
- CostCenter default neutralized (`CHANGE-ME`); real value lives only in the
  private overlay (org tag policy rejects arbitrary values).

App (staffops-aigent-squad):
- `Boto3Adapter` builds clients with explicit `region_name=settings.aws_region`
  (botocore reads AWS_DEFAULT_REGION, not AWS_REGION → NoRegionError). EC2/RDS/CE
  now return real data.
- `Classifier._extract_json`: strips ```json fences / preamble before json.loads
  → structured parse instead of the low-confidence "Fallback parsing" path.
  Confirmed live: confidence 0.98, correct routing.
- Terraform: `bedrock-aip` output `arn`, IAM ApplyGuardrail + DescribeTable,
  example parametrized (ns/SA), CostProject uppercase. `*.auto.tfvars` gitignored
  (was leaking real VPC/subnets/cost_center).

### Homologated (real queries via the public endpoint)
- "AWS services I use most" → 695 EC2 / 670 S3 / 100 IAM / 10 RDS (real).
- "cost trend 30d" → $191,225.98 from Cost Explorer; confidence 0.98.
- Bumped gateway `FIRST_BYTE_TIMEOUT` 15→30s (finops Athena+Bedrock ≈17s).

### NOT committed yet (single milestone commit pending)
All the above is on disk, uncommitted (user wants one big commit). Commit-time
TODO: publish chart 0.9.0 + revert the local-path override in the helmfile;
neutralize `gateway.image.repository` (still a personal Docker Hub repo in the
public values); revert overlay `pullPolicy` Always→IfNotPresent; **revoke the
two PATs pasted in chat** (GitHub ghp_… + GitLab glpat-…).

---

## Done — session 2026-06-22 / 2026-06-23

### Root doc cleanup (spec 24)
- Deleted stale `VERSIONS.md` + `GENERIC_VERSION.md`; archived
  `IMPLEMENTATION_HISTORY.md` → `archive/`. Refs updated in README + ROADMAP.

### Spec 14 Phase 1 — Bedrock Guardrail + fail-closed (DONE ✅)
- `infra/terraform/guardrail/`: `aws_bedrock_guardrail` (PROMPT_ATTACK HIGH
  multi-language, PII BLOCK, content filters, optional denied topics) + published
  version; outputs id/version.
- `src/core/guardrail.py`: `GuardrailClient.apply()` input+output via
  `apply_guardrail` API; **fail-closed** (block OR unavailable → 403, never
  bypass); structured audit log (no cleartext payload, sha256 digest).
- Wired into `bedrock.invoke` (chokepoint for all LLM calls); does NOT trip the
  circuit breaker. Propagated through classifier/supervisor/investigation → 403
  at `/internal/process`. 99% cov on guardrail. Independent author + review.
- Phases 2–5 (canary, output filter, rate/budget L6, input scanner,
  multi-language suite) still pending.

### Spec 31 — Edge gateway + worker pool (L1–L4 DONE ✅, L5 partial)
- **L1/L2**: `src/gateway/` — thin FastAPI front door (edge auth, `WorkerPool`
  backpressure, OpenAI /v1 + /query + /jobs/{id}/cancel). Supervisor became
  backend-only (`:8001`, `/internal/process` + `/internal/agents`, gateway-only
  via `SUPERVISOR_INTERNAL_TOKEN`); public routes moved to the gateway.
- **L3**: `src/core/rate_limiter.py` — `AdmissionGuard` (per-user rate + global
  daily budget, Redis, **fail-open**) + `estimate_cost`. Enforced at the gateway
  before forward (429/503 with headers). Budget TOCTOU → hardening T19d.
- **L4**: chart in `StaffOps/helm-charts` (`aigent-squad` 0.8.0) — gateway +
  supervisor in the `services` map (KEDA per tier), supervisor NetworkPolicy
  locked to gateway-only, gateway `networkPolicy.allowFrom` opens it to in-cluster
  callers (Alertmanager, anomaly-detection, Falco). CostCenter `devops-team`.
  docker-compose two-tier + mcp-server repointed to the gateway. Both topologies
  (inProcess + distributed) aligned.
- **L5 partial**: `docs/site/architecture.md` + `metrics.md` rewritten two-tier.
  Pending: T21 (k6 load test), T23 (final independent review).
- Round-table sign-off (dev+security+sre+gitops) settled the 3 design questions.
- Concurrency model: per-replica pool (fail-open-to-reject) + global Redis
  rate/budget (fail-open-to-allow), distinct from spec-14 guardrail (fail-closed).

### Shipped (2026-06-23)
- PR #17 `dev → main` merged → `build.yml` built scan-gated multi-arch image with
  `src/gateway` → Docker Hub tags `latest` + `ba13399` (the `0.2.0` tag does NOT
  contain the gateway). helm-charts `main` pushed (chart 0.8.0), CI green.
- CI fixes found by watching pipelines: ruff F401 in L3 tests; added
  `fakeredis`+`respx` to `test.yml` (gateway test deps); ct-values pinned both
  tiers to `autoscaling.kind=none` (no CRD on bare kind).

### Release note
- No version bump yet. The gateway is a **new tier → MINOR `0.3.0`** (not a
  PATCH), to be cut once validated in a cluster (per `version-management`).
  Until then the image `latest`/`sha` carries the gateway; chart `appVersion`
  stays `0.2.0` with a ⚠️ in the chart README.

---

## Done — session 2026-06-21 / 2026-06-22

### Release v0.2.0 (first tagged release)
- App tag `v0.2.0` → `release.yml` built scan-gated multi-arch image
  `karlipegomes/aigent-squad:0.2.0` (+ `latest`) + SBOM + GitHub Release.
- Chart `helm-charts/aigent-squad` → `version 0.7.0`, `appVersion 0.2.0`;
  `image.tag` removed so it inherits `appVersion` (renders `:0.2.0`, never
  `latest` in prod). chart-releaser published `aigent-squad-0.7.0`.
- `CHANGES.md` cut `[0.2.0]`; new work accrues under `[Unreleased]`.

### CI/CD — Model A (`docs/CI-CD.md`)
- **Scan-before-publish** on `build.yml` AND `release.yml`: build local →
  Trivy gate → push only if clean. Vulnerable image never reaches the registry.
- `release.yml` rewritten: tag-driven (`v*`)/manual, Docker Hub, SBOM, Release
  (replaced legacy ECR/SSH).
- `test.yml`: `guard` (main accepts PRs only from `dev`) + `dep_scan` (Trivy fs).
- `sast.yml`: **Bandit** (CodeQL needs GHAS — unavailable on private repo). 4
  reviewed B104 (`0.0.0.0` bind) suppressed with `# nosec`.
- `docs.yml`: deploy ONLY from `main` (was overwriting prod from `dev`);
  `mkdocs build --strict` on PRs. Fixed broken Architecture page (case collision)
  + LIBRECHAT external links → strict-clean.

### Specs
- **Spec 10 Phase 1** — efficiency/quality metrics (collect/llm duration,
  prompt size, investigation rounds). 246 tests.
- **Spec 30** — datasource cache wired into adapters (sha256 TTL, fail-open at
  the adapter boundary); `aigent.cache.hits/misses` now emitted; `tokens_saved`
  reassigned to spec 11. 266 tests, 92.62% coverage. Tests by an independent agent.

### Workflow change — solo dev
- `dev` is now committed to **directly** (no `feature → dev` PRs). Every push to
  `dev` runs lint + test+coverage + dep_scan + bandit. `main` keeps the PR gate.
  Documented in `docs/CI-CD.md` + README.

### Misc
- Apache 2.0 migration; MkDocs site live at `staffops.github.io/aigent-squad/`;
  CI on `DOCS_DEPLOY_TOKEN` (HTTPS private dep + BuildKit secret); CVE cleanup
  + `.trivyignore`.

> **Sync note:** as of 2026-06-22, `dev` is 2 docs-only commits ahead of `main`
> (the direct-commit-flow docs). They reach the published site on the next
> `dev → main`. Not urgent.

---

## Done — session 2026-06-16 / 2026-06-17 (historical)

- **Migration**: code moved from `AIgent-squad` (public, frozen) → private
  repo `staffops-aigent-squad` with full history.
- **Terraform**: `iam/`, `dynamodb/`, `bedrock/`, `bedrock-aip/`. All validated.
- **Fix Bedrock**: `BEDROCK_MODEL_ID` needs inference profile `us.` prefix.
- **MCP adapter** (`type: mcp`): agents as MCP clients, read-only allowlist.
- **Skills** (spec 26): lazy-loaded markdown knowledge. 100% cov.
- **Cost attribution** (spec 27): AIP per model + metrics with `agent_id`.
- **Spec 14 (security)**: written, NOT implemented.
- **Efficiency/cost steering**: `efficiency-cost.md` new pillar.
- **Competitive analysis**: `docs/COMPETITIVE-ANALYSIS.md`.
- **Helm chart** (`helm-charts/charts/aigent-squad` 0.4.0): `services` map,
  two topologies (inProcess/distributed), HPA/KEDA/none, Ingress/GatewayAPI/none.
- **Claude Code compatibility**: `CLAUDE.md` + `.claude/` mirror.
- **OpenAI-compatible bridge** (spec 29): `/v1/models` + `/v1/chat/completions`
  on supervisor; `docs/LIBRECHAT.md`.
- **i18n**: all active docs/steering/prompts translated to English.

---

## Done — session 2026-06-18

### Spec 07 — Readiness probes (COMPLETE ✅)
- `src/core/health.py`: `DependencyChecker` — async checks (Redis, DynamoDB,
  Bedrock creds, HTTP) with per-dep TTL cache (5 s) + timeout (2 s).
- `src/supervisor/server.py`: `/healthz` (liveness), `/ready` (readiness),
  `/health` legacy alias.
- `mcp-server/mcp-server.py`: same probe set; `/ready` checks supervisor.
- `docker-compose.yaml`: healthchecks → `/ready`.
- `tests/test_health.py`: 237 tests total, 92.46% coverage.
- `Dockerfile.test`: reproducible test image (python:3.11-slim + SSH);
  run via volume mount, no rebuild on code change.

### CI/CD — Docker Hub (COMPLETE ✅)
- `.github/workflows/build.yml`: `build` job (multi-arch `linux/amd64,linux/arm64`
  → `karlipegomes/aigent-squad:latest` + `:sha-<short>`) + `scan` job (Trivy
  CRITICAL/HIGH + CycloneDX SBOM).
- ECR removed — Docker Hub is the only registry.
- `DOCKERHUB_USERNAME` + `DOCKERHUB_TOKEN` secrets configured on
  `StaffOps/staffops-aigent-squad`.

### Helm chart 0.6.0 (COMPLETE ✅)
- Default image: `karlipegomes/aigent-squad:latest` (Docker Hub, `registry: ""`).
- `Chart.yaml` home/sources → `StaffOps/` org.
- All READMEs updated (root + aigent-squad + staffops-anomaly-detection):
  `staffops.github.io`, `StaffOps/` org, chart badge 0.6.0.
- GitHub Pages configured: `staffops.github.io/helm-charts` (source: `gh-pages`
  branch, legacy mode). Branch `gh-pages` created.
- Helm repo: `helm repo add staffops https://staffops.github.io/helm-charts`.

### Spec 05 tasks.md cleanup
- T5 (Argo Rollouts) and T14 (per-env values files) removed — out of scope.
- "provider-agnostic" language removed from all chart files.
- Completed tasks marked `[x]`.

### Dockerfile → Alpine multi-stage (COMPLETE ✅)
- Builder: `python:3.11-alpine` + gcc/libffi/openssl/git/ssh (build-only).
- Runtime: clean `python:3.11-alpine` — no perl, no ncurses, no build tools.
- `wheel>=0.46.2` + `setuptools>=79.0.1` upgraded to fix CVE-2026-24049 and
  CVE-2026-23949.
- Remaining OS CVEs (perl, ncurses, sqlite) have `status=affected` in Debian 13
  — irrelevant now since Alpine has none of those packages.
- `Dockerfile.test` stays on `python:3.11-slim` (OTel/pkg_resources constraint).

### Org migration
- `staffops-aigent-squad` remote → `git@github.com:StaffOps/staffops-aigent-squad.git`
- `helm-charts` remote → `git@github.com:StaffOps/helm-charts.git`

---

## Current branch state

`dev` and `main` are in sync as of 2026-06-23 (PR #17 merged). The gateway image
is on Docker Hub (`latest` + `ba13399`); helm-charts `main` has chart 0.8.0.
Next work resumes on `dev`.

---

## Pending / Blockers

| # | Item | Notes |
|---|------|-------|
| 1 | **Branch protection NOT enforced** | Needs GitHub Pro/Team or a public repo (private Free → 403). `guard` job + CI checks run, but a direct push to `main` is technically possible. Deferred. |
| 2 | **`build.yml` uses personal Docker Hub** | Image is `karlipegomes/aigent-squad`; migrate to a StaffOps org namespace. |
| 3 | **Helm install on a real cluster** | ✅ DONE (2026-07-01) — deployed to devops-core via helmfile (`k8s-setup/staffops/`), image from Harbor labs. `ct install` on kind still not wired, but a real EKS install is validated. |
| 4 | **LibreChat end-to-end** | Bridge unit-tested + `/v1/models` validated via the public Istio endpoint (HTTP 200, 6 models). Not yet wired to a live LibreChat instance. |
| 5 | **Spec 31 gateway not cluster-validated** | ✅ DONE (2026-07-01) — gateway + supervisor running in devops-core, end-to-end queries hitting Bedrock. Image `0.3.0-dev` on Harbor. Chart bumped to 0.9.0 (uncommitted). |
| 6 | **Budget TOCTOU (spec 31 L3)** | ✅ DONE (2026-07-02) — atomic Lua `EVAL` check-and-reserve; `fakeredis[lua]` in CI. |
| 7 | **Spec 14 Phases 2–5** | ✅ DONE (2026-07-02→03) — L1–L6 all shipped, multilingual attack-suite CI gate, SECURITY.md defense-in-depth, cluster-homologated. |
| 8 | **Distributed topology (code)** | Chart renders it but supervisor only routes in-process — needs a RemoteAgent HTTP client. Deferred to backlog (ADR-001 favors in-process). See ROADMAP backlog. |
| 9 | **finops ↔ Athena** | finops agent declares an `athena` datasource but IRSA has `enable_athena_finops=false` → AccessDenied. Enable Athena or drop the datasource. See ROADMAP backlog. |
| 10 | **Spec 14 entry-point findings A/B/D** | Homologation (2026-07-03) found the supervisor/classifier entry is under-instrumented + under-protected: A (classifier drops user_id/session_id → unattributable audit), B (homoglyph evades classifier L1 — worker-only L2), D (oversized ValueError pre-empts scanner → HTTP 200). Fixes proposed, deferred. Detail in spec 14 tasks.md. **Next security work.** |
| 11 | **Push pending** | app `fea17d2` + overlay `c1c9195` committed locally, awaiting push approval (GitHub `dev` + GitLab `main`). |

---

## Next specs (priority order)

> Done since last handoff: **spec 14 complete (Phases 1–5, L1–L6 + attack-suite
> CI gate + docs)** and cluster-homologated on devops-core; spec 11 (model
> tiering); budget TOCTOU (Lua atomic); Redis StatefulSet+PVC (chart 0.9.2);
> ElastiCache validate-and-destroy. Image `0.3.0-dev` rebuilt with InputScanner.

1. **Push the two local commits** — app `fea17d2` (→ GitHub `dev`, runs CI) +
   overlay `c1c9195` (→ GitLab `main`). Confirm CI green after.
2. **Spec 14 entry-point fixes A/B/D** (Pending #10) — the next security work.
   Pipeline dev→test→security: propagate `user_id`/`session_id` through the
   classifier (A+C), wire `InputScanner` at `supervisor.process_request` (B),
   drop the redundant oversized `ValueError` (D). Re-homologate after.
3. **Cut `0.4.0`** — gated on closing A/B/D (anti-injection validated end-to-end
   at the entry point). Don't bump before.
4. **Side issue** — aws agent leaks `<use_mcp_tool>` XML in the response instead
   of executing the tool. Investigate separately.
5. **Distributed topology (code)** + **finops↔Athena** — backlog (deferred).
6. **Spec 28 — RCA benchmark** (OpenSRE CloudOpsBench pattern).
7. **Branch protection** — once the GitHub plan allows (Pending #1).

---

## Metrics gaps — closed by spec 10 Phase 1 (2026-06-18)

- [x] `aigent.tokens.total` / `aigent.cost.estimated`: `model` label — already present in `bedrock.py` (spec 27).
- [x] `aigent.prompt.size_tokens` histogram — detect bloated prompts (bedrock.py).
- [x] `aigent.investigation.rounds` histogram — real rounds vs cap (investigation.py).
- [x] `aigent.llm.duration` separate from `aigent.collect.duration` — both shipped (bedrock.py + generic_agent.py).
- [x] `docs/METRICS.md`: reorganized by purpose (RED / Efficiency / Quality / Domain) + "Known gaps" section.
- [ ] `aigent.cache.hits/misses`: **defined but never emitted** — datasource cache not wired into adapters. Deferred.
- [ ] `aigent.cache.tokens_saved` counter: not defined; depends on datasource cache. Deferred to a future `datasource-cache-layer` spec.

---

## What may have been missed

- **Deploy to AWS**: nothing applied. All Terraform is `validate`-only.
  The jump from "code" to "running in prod" is the biggest remaining work.
- **Spec 14 is design only** — easy to forget security "is done" when only
  the plan exists.
- **Historical specs 01-25 remain in PT** (frozen record, low priority).
- **`staffops-agent-config` agents** have `datasources: []` and keyword
  placeholders — need real tuning before production use.
