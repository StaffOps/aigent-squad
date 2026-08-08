# .claude/ — executable wiring for Claude Code sessions

**Contract (spec 36, Decision 3):** `AGENTS.md` is the canonical, tool-agnostic
guide — ALL rules and knowledge live there (and in `specs/`, `RELEASE.md`,
`docs/`). This directory contains **zero rule prose**: only executable sugar —
pre-approved permissions and step recipes (skills) that *reference* the
canonical docs. If a skill needs to explain a rule, that rule belongs in
AGENTS.md and the skill links to it. Drift here is a review-blocker.

| File | Purpose |
|------|---------|
| `settings.json` | Permission allowlist for the safe, recurring commands (make targets, docker, pytest-in-docker, ruff, git/gh read-ops) — grants are reviewable via git history instead of re-approved every session |
| `skills/verify/` | Bring the stack up + smoke + where to look when it fails |
| `skills/run-tests/` | Local test harness incl. the private-dep stub caveat |
| `skills/new-agent/` | Scaffold a new agent (config-only) |
| `skills/harness-score/` | Measure/raise AI-agent harness maturity (`make harness-score`); floor + anti-gaming rule lives in AGENTS.md |
| `skills/release/` | Pointer to RELEASE.md (lands with spec 34) |

Other AI-tool dirs (`.cursor/`, `.kiro/`, …) remain git-ignored — they get the
same knowledge from `AGENTS.md`.
