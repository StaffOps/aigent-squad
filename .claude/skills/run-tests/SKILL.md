---
name: run-tests
description: Run the aigent-squad test suite or a single test file locally via Docker, handling the private otel-helper dependency automatically. Use before any push.
---

# Run tests locally

- Full suite + coverage gate (CI-identical): `make test`
- One file: `make test-one FILE=tests/test_gateway_main.py`
- Lint (ALWAYS before push, CI-verbatim scope): `make lint`

## The private-dep stub (read this once)

`otel-helper` comes from a private repo. `scripts/test-local.sh` detects
reachability; without access it generates a no-op stub and filters the
requirement. **A stubbed run does not validate telemetry wiring or the real
dependency set** — the script prints a loud warning. Treat CI as the verdict:

```
git push && gh run list --branch $(git branch --show-current) -L 3
```

Rules that bite (from AGENTS.md, the canonical source):
- CI runs lint → test and STOPS at the first failure — a lint error HIDES test results.
- Lint the FULL scope (`src/ tests/`), not just touched files; `ruff --fix` clears F401.
- Coverage gate is ≥90%, measured in Docker — the local gate is the same command.
