# Design: Resilience Patterns + Async-First

## Architecture

Two layers on top of the unified agents (spec 02):

```
async-first        → all I/O non-blocking; Bedrock calls parallelizable
   └── resilience → fail-open (Redis/DynamoDB) · circuit breaker (agents) ·
                     classifier fallback · retry w/ jitter · graceful shutdown
```

## Components

| Component | Responsibility | Where |
|-----------|----------------|-------|
| Async clients | Bedrock/DynamoDB via `aioboto3` or `asyncio.to_thread`; HTTP/GitLab via `httpx.AsyncClient` | `src/core/{bedrock,state_store,gitlab_client}.py` |
| Fail-open wrappers | Redis/DynamoDB: error → degradation, not exception | `src/core/{cache,state_store}.py` |
| Classifier fallback | rule/keyword OR last agent OR ask user to choose | `src/core/classifier.py` |
| CircuitBreaker | per agent; closed→open→half-open | `src/core/circuit_breaker.py` (new) |
| Retry+jitter | backoff with jitter + botocore adaptive | `src/core/bedrock.py` |
| Lifespan | graceful shutdown (drain, flush OTel, close pools) | each `server.py` |

## CRITICAL RATIONALE: where RCA velocity comes from

> Record from the 2026-06-02 analysis (decision not to invest in gRPC/topology/communication).

**The dominant cost is the model call (~5s). Everything else is noise.**

| Operation | Order of magnitude |
|-----------|-------------------|
| Bedrock/Claude/Kiro call | **~2,000–8,000 ms** |
| CLI-sidecar boot (chaitops model) | ~2,000–15,000 ms |
| HTTP between pods | ~1–3 ms |
| gRPC / same pod (localhost) | ~0.1–2 ms |

**Conclusion — RCA velocity comes from parallelizing model calls, not from speeding up the plumbing:**

| Strategy | 1 RCA with 5 agents | Nature |
|----------|---------------------|--------|
| Synchronous/blocking (current code) | 5 × 5s = **25s** | ❌ serial |
| **Async fan-out, 1 replica** | **~5s** (5 calls wait together) | ✅ **the gain** |
| gRPC vs HTTP | ~5s in both | irrelevant (Δ ms) |
| Same pod vs distinct pods | ~5s in both | irrelevant (Δ ms) + couples scale/failure |
| 10 replicas | still ~5s **for ONE** RCA | doesn't speed up 1 investigation |

**Async = velocity. Replicas = throughput. Pod topology = irrelevant (and same-pod hurts: couples scale and failure).**

- **Async (this spec) + fan-out (17)**: parallelize the N Bedrock calls → 25s becomes ~5s. It's the only time gain for 1 RCA.
- **Replicas**: do NOT speed up 1 RCA (an investigation doesn't split across replicas). They serve **throughput** — many simultaneous alerts. And with async code, **1 replica** already handles many concurrent RCAs (time is almost all I/O wait for Bedrock; during the wait the process works on others). Replicas only come in when 1 replica saturates (CPU/mem or Bedrock throughput quota).
- **gRPC / same pod**: ms gain on a seconds-long operation = 0.06%. Don't build for velocity (gRPC removed — see `ECOSYSTEM.md`).

### Why async is a PREREQUISITE (not optional)
`asyncio.gather` only parallelizes if the calls **release the event loop** during the wait (I/O await). Today (`bedrock.py` synchronous + `time.sleep`) the loop stays **blocked** — `gather` of blocking calls runs **in series**. Without async, spec 17's fan-out is an illusion: looks parallel, executes serial. That's why 06 comes before 17/18.

## Decisions and trade-offs

### Decision 1: Fail-open on non-critical dependencies (Redis, history)
**Choice**: Redis/DynamoDB unavailable → degrade (cache miss / empty history), don't fail.
**Justification**: cache and history are *accelerators*, not source of truth. Failing the entire query because of them = self-inflicted outage (CONV-2). RCA must work even with degraded backing.
**Trade-off**: responses without context/cache during the outage — acceptable (degradation > unavailability).
**When to reopen**: if a dependency becomes the source of truth (not the case).

### Decision 2: `asyncio.to_thread` for boto3, not full migration to aioboto3 now
**Choice**: where a mature async client exists, use it; otherwise, `asyncio.to_thread()` around the synchronous boto3.
**Justification**: `to_thread` solves the event loop blocking with minimal change and low risk; migrating everything to `aioboto3` at once is a large, risky refactor pre-MVP.
**Trade-off**: threads cost slightly more than native async — irrelevant at current volume.
**When to reopen**: if volume grows to where the thread overhead matters → migrate hot paths to aioboto3.

## Invariants
- No blocking I/O in `async` path; zero `time.sleep` in async code.
- Non-critical dependency **never** crashes the query (fail-open).
- Classifier **never** causes total failure (always has fallback).
- Circuit breaker and timeouts **configurable** (specs 19/22).

## External dependencies
| Lib | Usage |
|-----|-------|
| `aioboto3` / botocore `Config(retries=adaptive)` | Bedrock/DynamoDB async + retry |
| `httpx.AsyncClient` | HTTP agents / GitLab / docs |
| `fakeredis`, `respx`, `moto` | offline tests (spec 23) |

## Verification
```bash
docker run --rm -v "$PWD:/app" -w /app python:3.11-slim sh -c \
  "pip install -q -r requirements.txt -r requirements-dev.txt && pytest tests/ -v --cov=src --cov-fail-under=90"
```
Decisive test: `gather` of 3 mocked calls with `sleep(0.5)` completes in ~0.5s (parallel), not ~1.5s (serial).

## Risks
- `to_thread` misapplied still blocks → cover with parallelism test (time≈max).
- Badly calibrated circuit breaker opens too early → configurable thresholds + metrics.
- Fail-open masks real problem → always log + emit metric on the degraded path.
