---
name: release
description: Cut an aigent-squad release (dev → main → tag → chart → cluster). Use when asked to release, tag, or publish a version.
---

# Release

The canonical, ordered checklist is **`RELEASE.md`** (spec 34) — follow it
verbatim; do not improvise steps from memory.

> Spec 34 not implemented yet? Then STOP and say so — releases before the
> runbook exists require the human to drive (the 0.2.0/0.3.0 cycles were
> manual). Interim guardrails that always apply (AGENTS.md):
> - Work lands on `dev`; `main` only via PR from `dev` (guard job enforces).
> - Never re-tag: a bad release gets X.Y.Z+1.
> - Version bumps need a measurable result (steering `version-management`);
>   current gate lives in ROADMAP ("next bump candidate").
> - Before any push: `make lint && make test`, then `gh run list` after.
