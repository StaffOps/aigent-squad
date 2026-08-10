# Design: Dockerized Test Harness

## Architecture

A dockerized test stage, reusable between dev and CI, that runs `pytest` with coverage and mocks — no local Python, no real services.

```
requirements.txt + requirements-dev.txt ─▶ Dockerfile.test (python:3.11-slim, cached deps)
                                                  │
                          docker run -v $PWD:/app ─┤
                                                  ▼
                           pytest --cov=src --cov-fail-under=90
                          (fakeredis · botocore stubber/moto · respx)
                                                  │
                                   exit≠0 if <90%  ▼  coverage.xml + term-missing
                                              local dev  ==  CI (spec 08)
```

## `Dockerfile.test`

```dockerfile
FROM python:3.11-slim
WORKDIR /app
# cached deps layer (code changes more often than deps)
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt
# code enters via volume in `docker run` (dev) or COPY (immutable CI)
CMD ["pytest", "--cov=src", "--cov-fail-under=90", \
     "--cov-report=term-missing", "--cov-report=xml"]
```

Single command (dev):
```bash
docker build -f Dockerfile.test -t aigent-test .
docker run --rm -v "$PWD:/app" -w /app aigent-test
```

Or without a dedicated build (ad-hoc, aligned with `dev-environment.md`):
```bash
docker run --rm -v "$PWD:/app" -w /app python:3.11-slim sh -c \
  "pip install -q -r requirements.txt -r requirements-dev.txt && \
   pytest --cov=src --cov-fail-under=90 --cov-report=term-missing"
```

## `requirements-dev.txt`

```
pytest
pytest-asyncio
pytest-cov
fakeredis        # Redis without a server
respx            # mock httpx (supervisor↔agent)
moto             # mock AWS (DynamoDB/Bedrock) — or botocore Stubber
```

## `pyproject.toml` (test section)

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "--strict-markers"

[tool.coverage.run]
source = ["src"]
omit = ["*/tests/*"]
```

## Mocks per dependency (zero real services)

| Dependency | Mock | Why |
|------------|------|-----|
| Redis (`cache.py`) | `fakeredis` | tests fail-open (spec 06) without running Redis |
| DynamoDB (`state_store.py`) | `moto` / botocore `Stubber` | tests history + fail-open offline |
| Bedrock (`bedrock.py`) | botocore `Stubber` | tests retry/throttle/parse without cost or network |
| HTTP agents (`supervisor`) | `respx` | tests routing/fan-out/timeout without running agents |

## Rationale (decisions and trade-offs)

### Decision 1: Same harness in dev and CI (not two paths)

**Choice**: the CI (spec 08) runs **exactly** the same Dockerfile/command as dev.

**Justification, in order of strength**:
1. **Kills "passes locally, fails in CI"** — the #1 cause is environment divergence; a single harness eliminates it (steering `error-recovery` → "works locally/fails on target" loop).
2. **Steering mandates it**: `dev-environment.md` requires builds/tests via Docker; a harness is the canonical form.
3. **Cheap parity**: the same `python:3.11-slim` in both; no maintaining two configs.

**Accepted trade-offs**:
| Cost | Reality |
|------|---------|
| Build of the test image on first run | Deps layer cached; subsequent runs are fast |

**When it would be wrong**: if the CI needed a multi-version matrix (3.11 + 3.12) — then the harness becomes parameterizable; today the steering fixes 3.11.

### Decision 2: Mocks (offline), not real services in unit/contract

**Choice**: unit/contract with `fakeredis`/`moto`/`respx`; integration with real services remains out of scope (future).

**Justification**: offline tests are fast, deterministic, and cheap (chaitops does this: 186 tests/~5s, no services). Real Bedrock would cost money and add flakiness from throttling.

**Trade-off accepted**: mocks can diverge from real behavior → mitigated by *contract tests* (validate real payloads, e.g., Alertmanager v2) and by 1 dockerized smoke test in the CI from spec 08.

### Decision 3: Reuse the `staffops-chaitops` pattern

**Choice**: copy the chaitops approach (`requirements-dev.txt` + `pytest --cov` in `python:3.11-slim`, fakeredis+respx) rather than inventing from scratch.

**Justification**: already proven in that repo (see `ECOSYSTEM.md`); reduces risk and maintains consistency in the ecosystem.

## Invariants

- Tests run **without network** and **without real services** (unit/contract).
- Coverage **<90% = build failure** (exit≠0).
- `python:3.11-slim` (never 3.12 — steering).
- Dev and CI use **the same** harness.
- `requirements-dev.txt` does **NOT** enter the production image.

## External dependencies

| Lib | Usage |
|-----|-------|
| pytest, pytest-asyncio, pytest-cov | runner + async + coverage |
| fakeredis, respx, moto/botocore Stubber | offline mocks |

## Verification

The harness itself is verified by running the minimal spec suite without network:
```bash
docker run --rm -v "$PWD:/app" -w /app aigent-test    # must pass and report ≥90%
```
Confirm: exit≠0 when coverage is forced below 90% (gate test); zero network connections during the run.

## Risks

- Mock↔real drift → mitigate with contract tests + dockerized smoke in CI (spec 08).
- Bloated test image → `requirements-dev.txt` separated; multi-stage keeps prod lean.
- Misconfigured `asyncio_mode` → async tests silently skipped; cover in the review.
