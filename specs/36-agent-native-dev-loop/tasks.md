# Tasks: Agent-Native Dev Loop

> Do FIRST among the 2026-07-04 batch — it multiplies every later session (32–35, spec-14
> fixes, evals). Verification pipeline per `specs/README.md` (spec 32; until it lands,
> per the AGENTS.md workflow rules).

## Phase 1 — Out-of-box local loop

- [ ] T1: Local dev defaults — compose override / `.env.local` committed with
      `GUARDRAIL_ENABLED=false`, `REDIS_SSL=false`, dev tokens; `.env.example` stays
      prod-shaped. Fresh clone requires zero env edits for local run
- [ ] T2: `scripts/stub-otel.sh` + `scripts/test-local.sh` — auto-detect the private
      `otel-helper` dep; stub when unreachable; run the CI-identical pytest command;
      loud trailing warning when stubbed
- [ ] T3: `scripts/smoke.sh` — gateway `/ready` + 1 real `/query` + `/v1/models`;
      clear failure messages mapped to playbook entries (depends on: T1)
- [ ] T4: `Makefile` — `up`, `down`, `smoke`, `test`, `test-one FILE=…`, `lint`,
      `eval` (stub until spec 35), `specs-status` (stub until spec 32)
      (depends on: T1–T3)
- [ ] T5: Fix or fold `setup-local.sh` into `make up` (drop "v2.0", `docker compose`
      v2, real prerequisite checks) (depends on: T4)

## Phase 2 — Claude-native layer

- [ ] T6: Un-ignore `.claude/` (keep `.cursor/`, `.kiro/`, etc. ignored);
      `.claude/README.md` with the contract (AGENTS.md canonical; executors only)
- [ ] T7: `.claude/settings.json` — permissions allowlist: docker/compose, pytest-in-
      docker, ruff, make targets, `gh run list`/`pr view`, git read-ops (depends on: T6)
- [ ] T8: Project skills — `verify` (up+smoke+failure map), `run-tests` (harness +
      stub caveat), `new-agent` (scaffold per HOW-TO-NEW-AGENT), `release` (pointer →
      RELEASE.md; lands with spec 34) (depends on: T4, T6)

## Phase 3 — Guard + docs

- [ ] T9: CI dev-loop check — CI test job invokes `make lint test` (same-harness at the
      entrypoint level) (depends on: T4)
- [ ] T10: AGENTS.md playbook section — failure modes ≤3 lines each (403 fail-closed,
      401 which-token, empty history, works-locally-fails-CI) pointing at make targets;
      commands in AGENTS.md/QUICKSTART/README switched to make targets
- [ ] T11: Independent review — fresh-clone dry run from nothing; verify same-command
      guarantee local↔CI; `.claude/` contract respected (no rule prose)
      (depends on: T1–T10)

## Order
T1→T3→T4→T5; T2 parallel; T6→T7/T8; T9/T10; T11 closes.

## Notes
- Removing the private dep is ADR-0007 territory (OSS decision) — here we only automate
  coping with it.
- Local dev-safe defaults must never reach Helm values (ADR-0004) — RELEASE.md 403 probe
  is the backstop.
