---
spec: 36-agent-native-dev-loop
status: done-with-deferrals
completed: 2026-07-04
superseded_by: null
depends_on: []
deferred: ["T11 independent review (fresh-clone dry run + .claude contract)"]
---

# Feature: Agent-Native Dev Loop (Claude-ready repo — zero-friction execution)

**Spec**: `36-agent-native-dev-loop`
**Severity**: 🔴 Top priority (multiplier — every future session, human or AI, pays the
current friction tax)
**Origin**: agent-executability audit 2026-07-04. Confirmed friction: (1) fresh clone +
`docker compose up` + query **fails by default** (`GUARDRAIL_ENABLED=true` with empty
`GUARDRAIL_ID` refuses every invoke); (2) no Makefile / `scripts/` — every canonical
command lives only as prose in AGENTS.md; (3) `.claude/` is git-ignored — no committed
permissions/skills, so every session re-negotiates the same approvals; (4) the private
`otel-helper` git dep makes local tests require a manual "grep it out + stub" ritual
documented only in prose (root cause of "passes locally ≠ passes CI"); (5)
`setup-local.sh` is stale ("v2.0", `docker-compose` v1 binary check).
**Depends on**: — (do FIRST; specs 32–35 and all code work get cheaper after this)

---

## Thesis

The project is executed primarily *by* AI agents (Claude Code sessions) under human
direction — but the repo is not built *for* them. Everything an agent needs exists only
as prose (AGENTS.md) or tribal knowledge (the stub ritual, the guardrail-off flag, which
commands are safe). Prose gets re-read, re-interpreted and re-mistaken every session;
**executables and committed config get it right every time**. This spec turns the golden
paths into runnable artifacts while keeping `AGENTS.md` the canonical tool-agnostic
guide — the `.claude/` layer becomes thin *executors* that reference it, not a fork of it.

## User Stories

WHEN any agent or human clones the repo and runs ONE documented command THEN the local
stack SHALL come up and answer a real query — no undocumented env surgery (local defaults
must work out of the box; secure defaults stay for prod via Helm values).

WHEN tests are run locally THEN one command SHALL handle the private-dep problem
automatically (stub `otel_helper` when the SSH dep is unreachable) and run the same
pytest gate as CI — killing the manual grep/stub ritual.

WHEN a Claude session starts in this repo THEN pre-approved permissions for the safe,
frequent commands (docker run/compose, pytest-in-docker, ruff, gh read-ops, git
read-ops) SHALL already be committed — no re-prompting for the same operations every
session.

WHEN an agent needs a golden path (run the app, run one test file, add an agent, debug a
403/401, execute a release) THEN a **project skill** SHALL encode it as steps that
execute, with AGENTS.md/RELEASE.md as the referenced source of truth.

WHEN the out-of-box path breaks (compose up fails, smoke fails) THEN CI SHALL catch it —
the dev loop itself gets a check, so the golden path can't silently rot again (like
setup-local.sh did).

## Acceptance Criteria

- [ ] **Out-of-box local run**: `make up && make smoke` works on a fresh clone with only
      Docker + AWS creds. Local compose defaults flip security to dev-safe
      (`GUARDRAIL_ENABLED=false`, `REDIS_SSL=false`, dev tokens) via a committed
      `.env.local`/compose override — `.env.example` keeps prod-shaped values but the
      LOCAL path no longer requires editing it. Fail-closed remains the prod default.
- [ ] **`Makefile`** with the canonical verbs: `up`, `down`, `smoke`, `test`, `test-one
      FILE=…`, `lint`, `eval` (spec 35 hook), `specs-status` (spec 32 hook). Every
      target ≤1 screen, delegating to `scripts/`.
- [ ] **`scripts/test-local.sh`**: detects whether the private `otel-helper` dep is
      installable; if not, generates the minimal stub on PYTHONPATH automatically and
      runs the SAME pytest command as CI. Documented caveat printed at the end
      ("local run used the stub — confirm with `gh run list` after push").
- [ ] **`.claude/` committed** (un-ignored) with:
      - `settings.json`: permissions allowlist for the safe recurring commands
        (docker/compose, pytest-in-docker, ruff, `gh run list`/`pr view`, git
        status/diff/log, make targets);
      - project skills: `verify` (bring stack up + smoke + where to look when it
        fails), `new-agent` (agents/<name>/ scaffold per HOW-TO-NEW-AGENT),
        `run-tests` (local harness incl. stub behavior), `release` (thin wrapper →
        RELEASE.md, after spec 34);
      - `README.md` stating the contract: **AGENTS.md stays canonical**; `.claude/` is
        executable sugar; other tools (`.cursor/` etc.) remain git-ignored.
- [ ] **AGENTS.md playbook section**: common failure modes with causes and fixes
      (403 = guardrail/scanner fail-closed; 401 = which of the two tokens; empty
      history = DynamoDB fail-open; "works locally, fails CI" = stub masking), each
      ≤3 lines, pointing at the make targets.
- [ ] **`setup-local.sh`** fixed or replaced by `make up` (v2.0 branding gone,
      `docker compose` v2 syntax, checks that match reality).
- [ ] **CI check for the dev loop**: a job (or extension of test.yml) that runs
      `make lint test` — proving the Makefile path IS the CI path (same-harness
      principle from spec 23, extended to the entrypoint).
- [ ] Docs sync: README quick-start and QUICKSTART.md updated to the make-based flow.

## Out of scope

- Removing the private `otel-helper` dependency entirely (that is the OSS decision —
  ADR-0007; this spec only automates coping with it).
- Cursor/Copilot/etc. specific configs (stay git-ignored; AGENTS.md covers them).
- Devcontainer / Codespaces image — revisit if a second contributor appears.
- The quality eval content itself → spec 35 (this spec only wires `make eval`).
