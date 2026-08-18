# Handoff

> **Overwrite rule (spec 32, Decision 4)** — this file holds **only the current session
> + next steps**. It is *overwritten*, never appended. Previous content moves to
> `archive/handoffs/YYYY-MM-DD.md`. Permanent history lives in `CHANGES.md`; per-spec status
> in spec frontmatter + `specs/ROADMAP.md`.
>
> Prior sessions: `archive/handoffs/2026-07-16.md`, `archive/handoffs/2026-07-17.md`.

---

## Current session — 2026-07-23/24 (agentic28 + metrics + full i18n audit)

**Live tag on `devops-core` (test=prod):** `0.4.0-homolog-agentic28` (rev 61). Metric improvements
built but NOT yet deployed (would be **agentic29**).

### Shipped + LIVE (agentic28, validated on devops-core)
- **Tier routing was INERT in prod — FIXED.** `_resolve_tier_model` was only threaded in auto-route +
  fan-out; force_agent streaming, investigation, and the alertmanager webhook bypassed it → 17/17
  invocations were Sonnet. Fixed (all paths). **Opus 4.0 profile gone in-account → corrected to Opus 4.5**
  (`us.anthropic.claude-opus-4-5-20251101-v1:0`), enabled via overlay. Live-validated: simple→Haiku (2.4s),
  complex RCA→Opus 4.5 (`tier=deep`). `GATEWAY_FIRST_BYTE_TIMEOUT` 90→140.
- **MCP-usage prompt reinforcement** (git-synced): observability cross-signal RCA + Investigation Mode;
  kubernetes live read-only tools table; removed a stale "never invoke tools" instruction. spec 39 WS3
  RCA folded into observability → spec 39 `done-with-deferrals`. spec 38 FU-B (startup validation → lifespan).
- **Full English translation + exhaustive 543-file audit** — every file read/validated, ~72 translated
  PT→EN (faithful, gate rc=0). Residual PT is functional/intentional only.

### Committed LOCAL, NOT deployed/pushed (branch `fix/openai-compat-drop-system-messages`; base `06b0d70`
is the user's own openai-compat fix)
- **Observability metrics** (commits ebc7252/9796d3b/75fdc3c/6dbd93a): 3 fixes (streaming request
  undercount, guardrail double-count, investigation fanout error) + 5 new metrics (tool.call_duration,
  guardrail.blocks, bedrock.throttles, context.trimmed_messages, tier.classifier_confidence) + a wrong
  confidence-bucketize REVERTED. **Full harness GO** (code-review + double-count + observability + sre +
  security CLEAN GO). Needs rebuild+deploy (agentic29) to go live.
- The i18n translation + all docs commits.
- (GitLab prompts + k8s-setup overlay were pushed during the agentic28 deploy — those ARE live.)

---

## PENDING — full inventory (to zero the session)

### 🟢 Ready, needs go (deploy/push)
1. ~~**Deploy agentic29**~~ — ✅ DONE 2026-07-24. Built multi-arch, helm rev 63 on devops-core, healthy+functional. Metrics→VM gap FIXED: enabled `serviceMonitor.enabled=true` (k8s-setup overlay, commit a197015) — the app already exposes /metrics (otel-helper metrics_app) + chart had the ServiceMonitor template; vmagent (selectAllByDefault) now scrapes all 5 new + fixed aigent_* metrics into VM (homologated live).
2. **Branch reconcile + push** — all local work (i18n, metrics, docs, tier-fix code) is on
   `fix/openai-compat-drop-system-messages`, not merged to `dev`. Needs PR/cherry-pick.

### 🔵 Decisions (user asked to be notified before acting)
3. **Delete/merge candidates** (see BACKLOG "Audit findings"): specs 05/19 (superseded) · skills/oomkill-investigation
   (merge → root-cause-analysis) · `src/agents/*/__init__.py` (empty) · root `otel_helper/` (dup) ·
   `evals/results/*-superseded.json`. (tfstate flag was a VERIFIED false positive — resolved.)
4. **Functional PT — keep or strip?** config.py bilingual + triage keywords + eval/attack PT fixtures
   (removing degrades bilingual UX / weakens PT-attack tests).
5. **Cut 0.5.0?** milestone candidate; release action (bump pyproject/chart + tag + PR per RELEASE.md).

### 🔴 Fix-needed — pre-existing test drift (breaks CI; found by the audit)
6. `tests/test_b16_calibrated_honesty.py` — ImportError (`CALIBRATED_HONESTY` moved to config.py).
7. `tests/test_spec37_adapter_truncation_integration.py` — fixture 24k vs `MAX_TOOL_RESULT_CHARS` 40k.
8. ~38 tests assert the removed `~` tilde ("~N items" → "N items").
9. `tests/test_rate_limiter.py` — collection error.

### 🟡 Roadmap P3 (large, not started)
10. **B-16 Phase-2** — structured confidence / unverified_claims / groundedness.
11. **B-03** — feedback (thumbs) → KbDelta pipeline.
12. **spec 28** — provider abstraction (local + API-key models beyond Bedrock).

### ⚪ Minor / deferred (BACKLOG)
13. Changelog hygiene (3 stray pre-0.4.0 `[Unreleased]` headers) · doc nits (COMPETITIVE-ANALYSIS date,
    Dockerfile.test vestigial `github_token` secret, spec 25 status).
14. `time.sleep` in bedrock.py retry loop — verify (likely non-issue; `_sync` methods run in a threadpool).
15. spec 38 FU-A (Phase-2 dispatch doc) · spec 39 T3.4 (deterministic investigation.py, Phase-2) ·
    MCP roadmap (GitLab / Kubecost) · future agents · B-25 (cluster → Docker Hub tag) · F-008
    (specs_status deferred-match tooling). grafana-mcp Viewer token = DECLINED (not pending).
