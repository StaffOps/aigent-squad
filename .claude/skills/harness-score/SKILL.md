---
name: harness-score
description: Measure and raise this repo's AI-agent harness maturity (harness-score L0-L4). Use when the harness_score CI job fails, before raising MIN_LEVEL, or when adding agent-facing wiring (AGENTS.md, .claude/skills/, sensors, CI gates).
---

# Harness score

Rule (floor discipline + the anti-gaming rule): **AGENTS.md → Workflow rules →
"Harness gate"**. This file is the recipe only.

1. `make harness-score` — report + gate at the `MIN_LEVEL` floor (Makefile).
2. `make harness-score MIN_LEVEL=0` — report only, never fails. Use to see the
   score without gating.
3. Fix top-of-list items first — the CLI ranks by points and names the file.
4. Re-run step 1. To claim a new level, also flip `MIN_LEVEL` in the Makefile
   (and only then — see the rule).

## Checks expected to stay red

The scanner is generic; the repo-specific ground truth (`ruff.toml` as lint SSOT,
`.githooks/` instead of `.pre-commit-config.yaml`, no rule prose outside
`AGENTS.md`, `make` + skills instead of `.claude/commands/`) is stated in
AGENTS.md → Workflow rules → "Harness gate". Consequences for the procedure:

- CTX-03/04/05/06 fail because harness-score does not read `.claude/rules/`
  (it looks for `.cursor/rules/*.mdc`, `.windsurf/rules/`, `.clinerules/`,
  `.continue/rules`, `.github/instructions`, `.agents/rules`, or nested
  `AGENTS.md`/`CLAUDE.md`). Do not create those files; the legitimate fix is
  upstream recognition of `.claude/rules/` (`check_change.yml` issue template).
- CI-04 (pre-commit config) and SKL-03 (`.claude/commands/`) stay red by design.

## Definition of done

`make harness-score` exits 0 at the current floor, and every point gained maps
to something a tool actually reads or a pipeline actually runs. If you can't
name the tool that consumes a file you added, delete it.
