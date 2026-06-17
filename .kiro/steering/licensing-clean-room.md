# Licensing & Clean-Room — MANDATORY

A **mandatory**, non-negotiable rule: **never copy code from third-party
repositories** into our source. Learning from them is free; copying their
expression is not.

This rule exists because the project actively studies other open-source projects
(see `docs/COMPETITIVE-ANALYSIS.md`) with varied licenses that are **mutually
incompatible for mixing** (Apache-2.0, MIT, SigmaHQ/DRL, etc.). Copying would
contaminate the repo with attribution/license obligations we don't want to carry.

---

## CRITICAL: the line you do not cross

**Copyright protects the EXPRESSION (the literal code), not the IDEA.**

| Allowed (free) | Forbidden (mandatory not to do) |
|----------------|----------------------------------|
| Read and understand how a project does something | Copy/paste a third-party file or code snippet |
| Describe the pattern/concept with attribution | Translate their code line-by-line into our style |
| **Implement from scratch** from the understanding | Copy a file's literal structure (same order, same names, same copied logic) |
| Cite the project as inspiration in a spec/ADR | Copy configs/rules/datasets under their own license |

Conceptual inspiration ("use context-spill-to-disk", "pluggable provider",
"per-tool HITL") is **free** — those are ideas. The implementation is **ours,
from scratch**.

## Third-party dependencies (the clean path to reuse)

When reusing third-party code is genuinely the best option, do it via a
**declared dependency** (package manager), NEVER by pasting source:

- Add it as a pinned dependency (`requirements.txt`/`pyproject.toml`).
- **Verify and declare the license BEFORE adopting.** Flag it explicitly to the
  user (e.g. "litellm is MIT — OK to add?").
- Prefer permissive licenses (MIT, Apache-2.0, BSD). Flag copyleft (GPL/AGPL) as
  a conscious decision — it can contaminate.
- Unusual names / possible typosquatting → flag (see `code-quality`/security).

## Datasets, rules, and content (not just code)

The rule goes beyond `.py`: SigmaHQ rules, benchmark datasets, prompts,
templates, configs under their own license — same discipline. Reuse only with
the license respected and declared; prefer reimplementing/recreating from scratch.

## Attribution when inspired

When implementing something inspired by a studied project, **cite the source** in
the spec/ADR/comment ("pattern inspired by X") — intellectual honesty, and it
makes clear it is a reimplementation, not a copy.

## Anti-patterns

- ❌ Copying a snippet "just to start, I'll change it later"
- ❌ Translating a third-party file into our style and treating it as ours
- ❌ Adopting a dependency without verifying/declaring the license
- ❌ Copying rules/datasets/prompts under a license without respecting the terms
- ❌ Mixing code from incompatible licenses in the same repo

## When in doubt

If it is not clear whether something is an "idea" (free) or "expression"
(protected): **stop and ask the user**. Never assume you can copy.
