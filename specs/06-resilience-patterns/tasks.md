# Tasks: Resilience Patterns + Async-First

> Velocity prerequisite for spec 17 (fan-out) and 18 (RCA). Without async, `gather` runs serially.

- [x] T1: Make `bedrock.py` non-blocking (`asyncio.to_thread`/aioboto3); `time.sleep`→`asyncio.sleep`; retry with jitter + botocore `Config(retries=adaptive)` — done 2026-06-14
- [x] T2: Make `state_store.py` (DynamoDB) non-blocking + **fail-open** (fetch→[], save→log) (depends on: —) — done 2026-06-14
- [x] T3: `cache.py` (Redis) non-blocking + **fail-open** (get→None, set→swallow+log) — done 2026-06-14
- [x] T4: `gitlab_client.py` (requests→`httpx.AsyncClient`) + `docs_portal.py` async; pools per destination — done 2026-06-14 — N/A: files deleted in spec 22 Phase A (replaced by Adapter pattern)
- [x] T5: Classifier fallback (rule/keyword OR last agent OR ask user to choose) when Bedrock fails (depends on: T1) — done 2026-06-14
- [x] T6: `circuit_breaker.py` per agent (closed→open→half-open), configurable thresholds (depends on: —) — done 2026-06-14
- [x] T7: Supervisor — configurable timeouts + bulkhead (httpx pool per agent) + uses circuit breaker (depends on: T6) — done 2026-06-14
- [x] T8: Graceful shutdown via FastAPI `lifespan` (drain, flush OTel, close pools) in all 6 services — done 2026-06-14
- [x] T9 (test-author DIFFERENT from author): pytest ≥90% — **parallelism (time≈max)**, fail-open Redis/DynamoDB, classifier fallback, circuit breaker, retry+jitter (depends on: T1–T8) — done 2026-06-14
- [x] T10: Independent review (`code-review`): zero blocking in async path, correct fail-open, no stacked retry (depends on: T9) — done 2026-06-14
- [ ] T11: Smoke via Docker — mocked RCA with 5 agents completes in ~1× (parallel), not 5× (depends on: T10) — deferred (manual smoke only, no formal RCA mock test)

## Suggested order
T1/T2/T3/T4 in parallel; T5 (after T1); T6→T7; T8; T9→T10→T11.

## Notes
- **Async is the prerequisite for ALL velocity** (see rationale in design): async=velocity, replicas=throughput, pod topology=irrelevant.
- Don't migrate everything to aioboto3 at once — `asyncio.to_thread` where there's no mature async client.
- Verification pipeline (`verification-independence.md`): T1–T8/T11 author; T9 test-author in separate session; T10 code-review.
- Test harness: spec 23 (offline mocks).

## Status (2026-06-14)

**Completed**: T1–T10. Full async-first refactor (bedrock, state_store, cache, classifier fallback, circuit breaker, supervisor timeouts/bulkhead, graceful shutdown). Tests and code-review done.

**Notes**:
- T4 marked done/N/A: `gitlab_client.py` and `docs_portal.py` were deleted in spec 22 Phase A — their functionality is now handled by the generic Adapter pattern (HttpAdapter).
- T11 (formal smoke RCA mock test) deferred — manual smoke testing was performed but no formalized Docker-based RCA mock harness.

**Deferred**: T11 formal smoke test (low priority; manual validation confirmed parallelism works).
