# Handoff

> **Overwrite rule (spec 32, Decision 4)** — this file holds **only the current session
> + next steps**. It is *overwritten*, never appended. At the start of each new session's
> handoff write, the previous content moves to `archive/handoffs/YYYY-MM-DD.md`. Permanent
> history lives in `CHANGES.md`; per-spec status lives in spec frontmatter + the canonical
> table in `specs/ROADMAP.md` — not here.
>
> Prior sessions: `archive/handoffs/2026-07-16.md`, `archive/handoffs/2026-07-17.md`.

---

## Current session — 2026-07-23/24 (agentic28: MCP reinforcement, tier-routing fix, full i18n audit)

Live tag on `devops-core` (test=prod): **`0.4.0-homolog-agentic28`** (gateway + supervisor, rev 61).

### Shipped + live-validated
- **MCP bindings reinforced (prompts, git-synced):** observability got a cross-signal RCA section +
  **Investigation Mode** (metric→trace→log→profile, ≥3-signal gate) leveraging grafana-mcp; kubernetes
  got a live read-only tools table (helm/rollout/cert/mesh via kubectl-mcp) + a stale pre-agentic
  "never invoke tools" instruction was removed (it was suppressing MCP use). Harness caught 2 blockers
  (excluded Sift tool, missing `tempo_` prefix) — fixed.
- **spec 39 WS3 cross-signal RCA — FOLDED into observability** (round-table verdict: no dedicated `rca`
  agent). spec 39 marked **done**. T3.4 (deterministic `investigation.py` path) = Phase 2, trigger not met.
- **🔴 CRITICAL FIX — tier routing was INERT in prod** (17/17 Sonnet, 0 tier logs). `_resolve_tier_model`
  was only threaded in auto-route + fan-out; **force_agent streaming, investigation, and the alertmanager
  webhook bypassed it**. Fixed via dev→dev-test→code-review (force_agent heuristic complexity;
  investigation/alertmanager = complex). **Opus 4.0 profile is gone in-account → corrected to Opus 4.5**
  (`us.anthropic.claude-opus-4-5-20251101-v1:0`), enabled via overlay. Live-validated: simple→Haiku (2.4s),
  complex RCA→Opus 4.5 (`tier=deep`). Raised `GATEWAY_FIRST_BYTE_TIMEOUT` 90→140 (Opus first-byte ~100s).
  Added ONE pertinent metric `aigent.tier.routing_decisions{tier}` (3 series). spec 38 FU-B (startup
  validation → FastAPI lifespan) also done. 103 tier tests.
- **Full English translation + exhaustive 543-file audit** — every file read + validated; ~72 files
  translated PT→EN (faithful, gate rc=0, tests green). Residual PT is functional/intentional only.

### Git state
- **Pushed:** GitLab `main` (prompts, git-synced), k8s-setup `main` (overlay Opus flip). LIVE.
- **Local, NOT pushed** (branch `fix/openai-compat-drop-system-messages`; base `06b0d70` is the user's
  own openai-compat fix): all app code + docs + the full i18n translation. App branch → `dev` reconcile
  is a pending PR.

## Next steps (need user decision — see `specs/BACKLOG.md` audit register)
1. **Delete/merge candidates (await approval):** specs 05/19 (superseded), skills/oomkill (merge),
   empty `src/agents/*/__init__.py`, root `otel_helper/` dup, tracked `terraform.tfstate(.backup)`,
   `evals/results/*-superseded.json`.
2. **Fix-needed (pre-existing test drift, breaks CI):** test_b16 `CALIBRATED_HONESTY` import,
   test_spec37 truncation fixture (24k vs 40k), ~38 tests asserting the removed `~` tilde.
3. **Functional-PT keep decision:** confirm keeping bilingual config/triage + PT eval/attack fixtures.
4. **Branch reconcile + push** the local app work to `dev` (PR).
5. **P3 features (large, focused sessions):** B-16 P2 (structured confidence), B-03 (feedback→KbDelta),
   spec 28 (provider abstraction).
6. **Version:** candidate `0.4.x → 0.5.0` once the above lands + validates (version-management).
