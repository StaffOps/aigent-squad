# Design: Agent-Native Dev Loop

## Architecture (prose → executables, one canonical entrypoint)

```
AGENTS.md (canonical, tool-agnostic)        ← unchanged role: the WHY + rules
     │ referenced by
     ▼
Makefile ── up / down / smoke / test / test-one / lint / eval / specs-status
     │ delegates to
     ▼
scripts/            .claude/  (committed)         local defaults
  test-local.sh       settings.json (permissions)   compose dev override:
  smoke.sh            skills/verify, new-agent,     GUARDRAIL_ENABLED=false
  stub-otel.sh          run-tests, release          REDIS_SSL=false, dev tokens
     │
     └── CI runs the SAME make targets (same-harness principle, spec 23 extended)
```

## Rationale (decisions)

### Decision 1: Makefile as the single command surface (prose demoted to reference)

**Choice**: every golden-path action gets a make target; AGENTS.md/QUICKSTART point at
targets instead of embedding raw docker one-liners.

**Justification, in order of strength**:
1. **Agents execute commands, not paragraphs.** The current failure mode is real: the
   stub ritual and the CI-verbatim-lint rule exist ONLY as prose, and AGENTS.md itself
   had to grow a warning box because sessions kept getting it wrong. An executable
   encodes the rule once.
2. **Drift becomes detectable.** When CI calls `make lint test`, a broken Makefile
   breaks CI — unlike QUICKSTART prose, which rotted for a month unnoticed (still said
   ports 8001–8005 until 2026-07-03).
3. Make is already on every dev/CI machine; zero new tooling (bash scripts under
   `scripts/` do the work; make is just the addressable index).

**Trade-offs accepted**:
| Cost | Reality |
|------|---------|
| One more file to maintain | It REPLACES scattered prose commands, net negative maintenance |
| Make quirks (tabs, .PHONY) | Targets are 1–3 lines delegating to scripts; complexity lives in bash with `set -euo pipefail` |

**When this would be wrong**: if the team standardizes on a task runner (just/task) —
mechanical port, same design.

### Decision 2: dev-safe local defaults via compose override — prod stays fail-closed

**Choice**: a committed compose override (or `.env.local` consumed by compose) sets
`GUARDRAIL_ENABLED=false`, `REDIS_SSL=false` and dev tokens for the LOCAL stack only.
`.env.example` keeps prod-shaped values; Helm values keep fail-closed defaults.

**Justification**:
1. **The out-of-box path must succeed** — a fresh clone that fails its first query
   (guardrail refusing with empty ID) teaches every new session that "the tool is
   broken", the most expensive first impression an agent-executed repo can make.
2. Splitting local vs prod defaults preserves ADR-0004 exactly: fail-closed is a
   *production* security semantic; a laptop without a provisioned Bedrock Guardrail is
   not a threat surface the guardrail can even evaluate.
3. Explicit file > flag folklore: today the fix is a comment inside `.env.example`
   telling you to edit it — the definition of tribal knowledge.

**Trade-off accepted**: someone could copy dev defaults to prod — mitigated: Helm values
(the only prod path) never read the local override, and RELEASE.md homologation includes
the 403 probe that would catch a disabled guardrail immediately.

### Decision 3: `.claude/` committed as thin executors; AGENTS.md stays canonical

**Choice**: reverse the "all tool dirs git-ignored" rule for `.claude/` ONLY, with a
strict contract: skills/settings reference AGENTS.md/RELEASE.md, never restate rules.

**Justification**:
1. The project is, in practice, developed BY Claude sessions — permissions re-prompting
   and golden-path re-derivation are a per-session tax with a one-time fix.
2. The tool-agnostic principle survives because the knowledge stays in AGENTS.md; what
   gets committed is *executable wiring* (allowlist + step recipes). A Cursor user loses
   nothing they have today.
3. Committed settings make agent behavior REVIEWABLE — permission grants go through git
   history instead of ephemeral session approvals.

**Trade-off accepted**: one tool gets first-class treatment — honest reflection of how
the project is actually built; revisit if the toolchain changes.

### Decision 4: auto-stub for the private dep, loudly

**Choice**: `scripts/test-local.sh` tries the real `otel-helper`; on failure generates
the minimal stub into a temp dir on PYTHONPATH, runs the CI-identical pytest command,
and prints a trailing warning that the stub was used.

**Justification**: the ritual is deterministic — exactly what scripts are for. Keeping
the warning loud preserves the AGENTS.md truth that stubbed local runs can mask
coverage/dep issues ("confirm with `gh run list`"). Removing the private dep itself is
an OSS-decision matter (ADR-0007), out of scope here.

## Invariants

- `make test` locally and CI's test job execute the **same** pytest command (only the
  dep-acquisition path may differ, and the local path announces itself).
- Local dev-safe defaults NEVER leak into Helm/prod values (ADR-0004 intact).
- `.claude/` contains zero rule prose — only pointers + executables (contract in its
  README; drift there is a review-blocker).
- Every make target is idempotent and safe to re-run.

## Verification

- Fresh-clone dry run (no repo-specific env prepared): `make up && make smoke` green;
  `make test` green with the stub warning printed; `make lint` matches CI verbatim.
- CI: the dev-loop job runs `make lint test` — a Makefile regression fails the build.
- Negative: unset AWS creds → `make smoke` fails with the *documented* error message
  (playbook entry), not a stack trace scavenger hunt.

## Risks

- Skills/settings rot as the app evolves → they contain pointers + make targets, and
  the CI dev-loop job guards the targets themselves.
- Dev-safe defaults used to "fix" a prod issue → RELEASE.md 403 probe + Helm-only prod
  path make it detectable at homologation.
