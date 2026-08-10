---
spec: 23-test-harness-docker
status: done
completed: 2026-06-18
superseded_by: null
depends_on: []
deferred: []
---

# Feature: Dockerized Test Harness

**Spec**: `23-test-harness-docker`
**Severity**: 🔴 High (without this, there is no way to run/validate tests — machine has no local SDK)
**Origin**: user requirement (improve tests with Dockerfile); steering `dev-environment.md` (builds/tests only via Docker), `verification-independence.md` (gate ≥90%)
**Relation**: operationalizes the coverage gate used by ALL specs with code (06–22). Spec `08-ci-cd-pipeline` consumes this harness in the CI stage.

Current state ✅ verified: **zero** tests, no `pytest`/`tox`, no test deps, no CI. The machine **has no local Python** (steering). Therefore, running tests = running in a container. This spec defines the **reusable dockerized test harness**: a test Dockerfile (or multi-stage target) + a single command that installs deps, runs `pytest` with coverage and **fails below 90%**, without real services (mocks).

## User Stories

WHEN a dev runs tests locally THEN they SHALL use **a single Docker command** that installs deps, executes `pytest`, and reports coverage — without installing Python on the machine.

WHEN tests run THEN external dependencies (Redis, DynamoDB, Bedrock, HTTP agents) SHALL be **mocked** (fakeredis, moto/stubber, respx) — **no real service** needed.

WHEN coverage falls **below 90%** THEN the command SHALL return exit code ≠ 0 (build gate).

WHEN the CI (spec 08) executes THEN it SHALL reuse **the same** harness/Dockerfile (dev↔CI parity — no environment divergence).

WHEN the test image is built THEN it SHALL use `python:3.11-slim` (not 3.12, due to `pkg_resources`/OTel — steering) and cache the deps layer.

WHEN tests finish THEN the coverage report SHALL be exportable (term + xml/html) for inspection and for the CI.

## Acceptance Criteria

- [ ] `Dockerfile.test` (or `test` stage in the multi-stage Dockerfile) based on `python:3.11-slim`, with runtime deps + `requirements-dev.txt` (pytest, pytest-asyncio, pytest-cov, fakeredis, respx, moto).
- [ ] A single documented command runs everything, e.g.:
  `docker run --rm -v "$PWD:/app" -w /app <img> sh -c "pytest --cov=src --cov-fail-under=90 --cov-report=term-missing --cov-report=xml"`.
- [ ] Deps layer cached (copy `requirements*.txt` before the code).
- [ ] **Zero real services**: Redis→fakeredis, DynamoDB/Bedrock→moto/botocore stubber, HTTP agents→respx. Tests run offline.
- [ ] Gate `--cov-fail-under=90` (exit ≠ 0 below it) — aligns with `verification-independence.md`.
- [ ] `pytest.ini`/`pyproject.toml` with test config (asyncio mode, paths, markers).
- [ ] `requirements-dev.txt` separate from runtime (does not bloat the production image).
- [ ] Report `coverage.xml` (for CI) + `term-missing` (for dev).
- [ ] CI (spec 08) reuses exactly this harness (same Dockerfile/command).
- [ ] README section "Running tests" with the single command.
- [ ] Tests (test-author ≠ author): the harness itself validated running the minimal suite from the specs (classifier, cache key, contract, fail-open) without network.

## Out of scope

- The feature test suites themselves — each spec (06–22) brings its own tests; here it is just the **harness**.
- Integration tests with real services (docker-compose for testing) — future; the MVP is unit/contract with mocks.
- Multi-arch of the test image (amd64+arm64) — production yes (spec 08); the test image runs on the runner's arch.
- Mutation testing / property-based — future.
