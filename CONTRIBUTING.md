# Contributing

Thanks for looking at AIgent-squad. This is a small, solo-maintained project —
issues and PRs are welcome, but response time may vary. Read `AGENTS.md` first;
it's the canonical guide (architecture, invariants, build/test commands,
prohibitions) and everything below assumes you've skimmed it.

## Before you start

- **Spec-driven**: non-trivial changes start with a spec under `specs/<NN-name>/`
  (`requirements.md` + `design.md` + `tasks.md`). **`specs/README.md` is the
  process guide** — lifecycle, status frontmatter (the SSOT), full-spec vs
  `bugfix.md` tiers, the verification pipeline, and when a security review is
  mandatory. Check `specs/ROADMAP.md` for what's already planned before proposing
  something new — it may already have a spec number and a design decision you'd
  want to align with.
- **Open an issue first** for anything beyond a small fix (typo, obvious bug,
  a test gap) — saves both of us from a PR built on a misunderstanding of intent.
- **Read the prohibitions in `AGENTS.md`** before writing code — a few of them
  are easy to trip on by habit (e.g. `datetime.utcnow()`, native `hash()` in
  cache keys, write/mutation commands — this system is read-only by design).

## Local setup

Everything runs via Docker + `make` — no local Python environment needed.

```bash
make up          # local two-tier stack + wait for gateway /ready
make smoke       # health + 1 real query + /v1/models
make test        # full suite + 90% coverage gate
make lint        # ruff, CI-verbatim scope
make down        # stop (V=1 drops volumes)
```

See `AGENTS.md` → "Build, test, lint" for the full command reference and
`docs/PREREQUISITES.md` for what you need installed (Docker is the only hard
requirement; AWS credentials are only needed for real Bedrock calls, not for
running the test suite).

**Real Bedrock/AWS calls cost money and need real credentials** — the test
suite (`make test`) never makes them; only `make eval` / `make eval-rca` /
running the live stack against a real `AWS_REGION`+credentials do. If you
don't have AWS access, you can still build, lint, and run the full test
suite — you just can't exercise the LLM-backed paths live.

## Making a change

1. Fork, branch off `dev` (not `main` — `main` only receives merges from `dev`
   via PR, see `RELEASE.md`).
2. Write the code + tests together. `steering/milestone-criteria.md` has the
   full checklist; the short version: tests pass, coverage stays ≥90%, any new
   feature emits ≥1 metric (`src/core/metrics.py` + `docs/METRICS.md`), and the
   relevant `docs/` file is updated in the same change (a pre-commit hook can
   enforce this locally — `make install-hooks`, opt-in).
3. `make lint` clean, `make test` green, on the FULL scope (not just files you
   touched — `ruff check src/ tests/`).
4. Conventional commit messages: `feat/fix/docs/test/refactor/chore(scope): description`.
5. Open a PR against `dev`. CI runs lint → test and stops at the first
   failure — if lint fails, you won't see test results, so run lint locally
   first.

## Adding a new agent

Config-driven, no code changes needed — see `docs/HOW-TO-NEW-AGENT.md`.

## Reporting a security issue

Please don't open a public issue for a security vulnerability — see
`docs/SECURITY.md` for the reporting path (or, if that doc doesn't yet list
one, open a private security advisory on GitHub).

## Licensing

Apache-2.0 (see `LICENSE`). **Never copy code from third-party repositories**
into a PR — see `steering/licensing-clean-room.md` for the (mandatory) rule:
learning from another project's approach is fine, copying its literal
expression is not. If you're adding a new dependency, flag its license in the
PR description.

## Code of conduct

Be respectful, assume good faith, keep disagreements about the code, not the
person. No formal CoC document yet for a project this size — if that becomes
a problem, it'll get one.
