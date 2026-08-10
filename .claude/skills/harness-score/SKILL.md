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

## Repo-specific facts that change what you should fix

The scanner is generic; this repo is not. These are measured facts, not opinions:

- **`.claude/rules/` is not a location harness-score reads.** It looks for
  `.cursor/rules/*.mdc`, `.windsurf/rules/`, `.clinerules/`, `.continue/rules`,
  `.github/instructions`, `.agents/rules`, or nested `AGENTS.md`/`CLAUDE.md`.
  So CTX-03/04/05/06 will fail here — and the fix is **not** to create those
  files. Our knowledge lives in `AGENTS.md` by contract (`.claude/README.md`),
  and `.claude/` holds zero rule prose. Upstream recognition of `.claude/rules/`
  is the legitimate fix: the project invites it (`check_change.yml` issue
  template).
- **`ruff.toml` is the lint SSOT** and takes precedence over `pyproject.toml`.
  A `[tool.ruff]` block in `pyproject.toml` is dead config.
- **Pre-commit is `.githooks/` + `core.hooksPath`** (`make install-hooks`).
  A `.pre-commit-config.yaml` is mutually exclusive with it — adding one
  satisfies CI-04 while enforcing nothing.
- **SKL-03 wants `.claude/commands/`**; this repo deliberately uses `make`
  targets + `.claude/skills/` instead. Expect that check to stay red.

## Definition of done

`make harness-score` exits 0 at the current floor, and every point gained maps
to something a tool actually reads or a pipeline actually runs. If you can't
name the tool that consumes a file you added, delete it.
