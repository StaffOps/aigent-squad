# Tasks: Dockerized Test Harness

> Verification prerequisite for ALL specs with code (06–22). Spec 08 (CI/CD) reuses this harness. Reuses the pattern from `staffops-chaitops`.

- [ ] T1: `requirements-dev.txt` (pytest, pytest-asyncio, pytest-cov, fakeredis, respx, moto) — separate from runtime
- [ ] T2: `Dockerfile.test` (`python:3.11-slim`, cached deps: COPY requirements*.txt before code) (depends on: T1)
- [ ] T3: Test config in `pyproject.toml` (`asyncio_mode=auto`, `testpaths`, `[tool.coverage.run] source=src`)
- [ ] T4: Single documented command + gate `--cov-fail-under=90` (`--cov-report=term-missing,xml`) (depends on: T2, T3)
- [ ] T5: Base mock fixtures in `tests/conftest.py` — fakeredis, botocore Stubber (Bedrock/DynamoDB), respx (HTTP agents) (depends on: T1)
- [ ] T6: Minimal offline suite (classifier parsing, cache key sha256, response contract, fail-open Redis/DynamoDB) proving the harness (depends on: T5)
- [ ] T7: Verify the gate — force coverage <90% and confirm exit≠0; confirm run without network (depends on: T4, T6)
- [ ] T8 (test-author DIFFERENT from author): review/expand minimal suite against contract (not mirror the implementation) (depends on: T6)
- [ ] T9: Independent review (`code-review`): correct mocks, zero network, effective gate, dev↔CI parity (depends on: T8)
- [ ] T10: README section "Running tests" (single command) + CI parity note (spec 08) (depends on: T4)

## Suggested order
T1→T2; T3; T4; T5→T6→T7; T8→T9→T10.

## Notes
- Reuse the chaitops approach (fakeredis+respx, `pytest --cov`, 3.11-slim) — do not reinvent.
- The CI (spec 08) MUST call the same Dockerfile/command — no second path.
- Offline mocks; integration with real services is out of scope (future).
- Verification pipeline (`verification-independence.md`): T1–T7/T10 author; T8 test-author in a different session; T9 code-review.
- Per `documentation-sync`: README gains the section in the same change.
