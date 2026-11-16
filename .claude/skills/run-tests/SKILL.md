---
name: run-tests
description: Run the aigent-squad test suite or a single test file locally via Docker, handling the otel-helper dependency automatically. Use before any push.
---

# Run tests locally

- Full suite + coverage gate (CI-identical): `make test`
- One file: `make test-one FILE=tests/test_gateway_main.py`
- Lint (ALWAYS before push, CI-verbatim scope): `make lint`

## The otel-helper stub (read this once)

`scripts/test-local.sh` **always** replaces the `otel-helper` requirement with a
no-op stub (the `python:3.11-slim` test container has no git), on every run —
it does not probe whether the real dependency is installable. **A stubbed run
does not validate telemetry wiring or the real dependency set** — the script
prints a loud warning. CI installs the real dependency, so treat CI as the
verdict:

```
git push && gh run list --branch $(git branch --show-current) -L 3
```

Pre-push rules (lint scope, CI stop-at-first-failure, coverage gate):
AGENTS.md → "Build, test, lint" → **Pre-push gate**.
