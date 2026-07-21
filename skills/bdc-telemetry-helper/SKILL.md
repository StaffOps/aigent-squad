---
name: bdc-telemetry-helper
description: "BDC OTel Helper monorepo overview \u2014 current state, structure, versions, sample apps, CI/CD pipeline, Harbor images. Use when working on the bdctelemetryhelper repo, releasing new versions, or referring to sample apps. Cross-references to detailed skills for specific topics."
keywords: [telemetry, harbor, otel, pipeline, ci, cd]
---
# BDC OTel Helper — Project Overview

Quick reference for the `bdctelemetryhelper` monorepo state.

## Repository

**Path**: `/home/karlipegomes/Documents/BDC/01-DEVOPS/LABS/bdctelemetryhelper`

GitLab: BDC's internal GitLab.

## Purpose

Corporate OpenTelemetry libraries for BDC apps:
- `.NET` library: `BDCOtelHelper`
- `Python` library: `bdc_otel`

Both implement the corporate observability standard. See related skill: `bdc-telemetry-standard`.

## Monorepo structure

```
bdctelemetryhelper/
├── README.md                          # Overview, env vars, quick start
├── .gitlab-ci.yml                     # CI: unit-test → build-dev → demo → build
├── .gitignore                         # .NET + Python + IDE + OS
├── dashboards/                        # Shared Grafana dashboards (used by all sample apps)
│   ├── 01-api-business-metrics.json
│   ├── 02-workers-background.json
│   └── 03-traces-reliability.json
├── dotnet/                            # .NET lib
│   ├── BDCOtelHelper/                 # Lib source
│   ├── BDCOtelHelper.Tests/           # 61 tests, 90% coverage
│   ├── BDCOtelHelper.sln
│   ├── example/
│   │   ├── dotnet-api/                # Minimal API (13 endpoints, gRPC client)
│   │   ├── dotnet-backend/            # gRPC server (5 RPCs)
│   │   ├── dotnet-process/            # Worker (4 background services)
│   │   └── Protos/order.proto
│   ├── .gitlab-cicd/
│   │   ├── 00_unit_test.yml
│   │   ├── 01_build_dev.yml
│   │   ├── 02_demo.yml                # Dual-arch (arm64 + amd64)
│   │   └── 03_build.yml
│   ├── README.md
│   ├── HOW-TO.md
│   └── TESTS.md
└── python/                            # Python lib
    ├── bdc_otel/                      # Lib source
    ├── tests/                         # 63 tests, 94% coverage
    ├── pyproject.toml
    ├── example/
    │   ├── python-api/                # FastAPI (notification service)
    │   ├── python-backend/            # gRPC server (notification delivery)
    │   ├── python-process/            # Workers (queue, retry, cleanup)
    │   └── protos/notification.proto
    ├── .gitlab-cicd/
    │   ├── 00_unit_test.yml
    │   ├── 01_build_dev.yml
    │   ├── 02_demo.yml                # Dual-arch (arm64 + amd64)
    │   └── 03_publish.yml
    ├── README.md
    ├── HOW-TO.md
    └── TESTS.md
```

## Current versions (May 2026)

### .NET library

| Item | Value |
|------|-------|
| Lib version | `0.1.0` (dev) |
| Target frameworks | `net8.0;net10.0` |
| OTel SDK | 1.15.3 |
| Tests | 61 passing, 90% coverage |
| API entry point | `services.AddBDCOtel()` |

### Python library

| Item | Value |
|------|-------|
| Lib version | `0.1.0` (dev) |
| Python | 3.11+ (NOT 3.12 — pkg_resources issues) |
| OTel SDK | opentelemetry 1.42.1 |
| Instrumentations | 0.63b1 |
| Tests | 63 passing, 94% coverage |
| API entry point | `setup_telemetry()` |

## Environment enum

```
LOCAL → log DEBUG, 100% sampling
DEV   → log INFO,  100% sampling
HML   → log INFO,  100% sampling
PRD   → log WARN,  100% sampling (tail-sampled at Collector)
BTC   → log WARN,  100% sampling (renamed from PRD_BATCH/PRD-BATCH)
```

Aliases: `PRD-BATCH`, `PRD_BATCH` → maps to `BTC`.

## Environment variables (both libs)

| Variable | Default | Description |
|----------|---------|-------------|
| `SERVICE_NAME` | `my-service` | Service name (priority 1) |
| `OTEL_SERVICE_NAME` | `my-service` | Fallback for service name |
| `ENVIRONMENT` | `LOCAL` | Environment enum |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel-agent-collector.monitoring` | Collector host |
| `BDC_OTEL_DEBUG_LEVEL` | `false` | Debug mode (force 100% sampling) |
| `BDC_OTEL_EXTRA_INSTRUMENTATION` | `SQL` | Conditional: SQL, AWS, REDIS |
| `BDC_OTEL_SAMPLE_RATIO` | `1.0` | Head sampling (0.0-1.0) |

## Sample apps — .NET

**Architecture:**
```
dotnet-process (Worker)
  HTTP :8080
  ↓
dotnet-api (Minimal API)
  gRPC :5100
  ↓
dotnet-backend (gRPC server)
```

**dotnet-api (13 endpoints):**

| Endpoint | Pattern Demonstrated |
|----------|---------------------|
| GET / | Health check |
| GET /order/{id} | Distributed trace (API→gRPC→Backend) |
| POST /order | POST with JSON body |
| GET /order/{id}/cancel | Write operation |
| GET /slow | High latency (4-7s) |
| GET /batch | Sequential fan-out (5 items) |
| GET /error | Exception → ERROR span |
| GET /health/ready | Dependency probe |
| GET /order/{id}/trace | Baggage propagation + scoped logging |
| GET /order/{id}/events | Span events (4 lifecycle events) |
| GET /parallel/{count} | Parallel fan-out (Task.WhenAll) |
| GET /retry/{id} | Retry with span per attempt |
| GET /cache/{id} | Cache hit/miss with metrics |

**dotnet-backend (5 RPCs):**

| RPC | What it simulates |
|-----|-------------------|
| ProcessOrder | DB query 10-80ms + httpbin ~1s |
| CancelOrder | DB update 5-30ms |
| SlowOperation | DB heavy 3-5s + external 1-2s |
| UnstableOperation | Transient failures (retry target) |
| ReadBaggage | Reads Baggage.Current, returns items |

**dotnet-process (4 workers):**

| Worker | Interval | Pattern |
|--------|----------|---------|
| ApiHealthWorker | 1 min | StartRootActivity per check |
| HeavyProcessWorker | 2 min | CPU/memory stress, parallel |
| QueueConsumerWorker | 30s | Consumer pattern, 1 trace/message |
| ScheduledJobWorker | 3 min | Timeout + circuit breaker |

## Sample apps — Python (notification service)

**Architecture:**
```
python-process (Worker)
  HTTP :8000 (with traceparent header)
  ↓
python-api (FastAPI :8000)
  gRPC :50051
  ↓
python-backend (gRPC server)
```

**python-api endpoints:**
- POST /notify → calls backend via gRPC
- GET /notify/{id} → status check
- GET /templates → list templates
- GET /health

**python-backend RPCs:**
- SendNotification → render + deliver (email/sms/push)
- GetStatus
- RenderTemplate

**python-process workers:**
- QueueConsumer (30s) → POST /notify
- RetryWorker (60s) → retries failed
- CleanupWorker (3min) → cleans expired
- HealthChecker (1min) → GET /health

## Harbor images

```
harbor.bigdatacorp.com.br/labs/dotnet-api
harbor.bigdatacorp.com.br/labs/dotnet-backend
harbor.bigdatacorp.com.br/labs/dotnet-process
harbor.bigdatacorp.com.br/labs/python-api
harbor.bigdatacorp.com.br/labs/python-backend
harbor.bigdatacorp.com.br/labs/python-process
```

Tags:
- `latest` — latest main build
- `<short_sha>` — CI immutable
- `<short_sha>-arm64-graviton` — Arm64 variant

## CI/CD pipeline (4 stages)

```
unit-test → build-dev → demo → build (manual)
```

| Stage | Trigger | Purpose |
|-------|---------|---------|
| `unit-test` | Every push | Run unit tests via Docker (61 .NET + 63 Python = 124 tests) |
| `build-dev` | Every push | Publish dev package (NuGet/PyPI/Docker dev tag) |
| `demo` | Every push | Ephemeral end-to-end (Docker run + curl + Tempo query) |
| `build` | Manual, main only | Publish stable package |

### demo stage details
- Dual-arch: arm64 + amd64 in parallel
- Build images → push Harbor → run via `docker run` → curl all endpoints → wait → query Tempo for traces → cleanup

### Versioning per language

| Lib | Format | Dev example | Release example |
|-----|--------|-------------|-----------------|
| .NET | SemVer 2.0 | `0.1.0-dev-a1b2c3d` | `1.0.0` |
| Python | PEP 440 (numeric only dev) | `0.1.0.dev42` | `1.0.0` |

## Profiling status

**Stand-by**. Pyroscope SDK packages removed (compatibility issues with OTel 1.15.3).

The `IProfilingProvider` interface and stub `PyroscopeProfilingProvider` remain in the codebase for future migration.

Will be re-enabled when:
1. Pyroscope packages updated, OR
2. OTel native Profiles signal stabilizes (currently Alpha, March 2026)

The `feat/pyroscope` branch (commit `824171a`) has the previous Pyroscope integration but is **not merged** to main (blocked by CPU limit issue — profiler requires ≥1 CPU in cgroup).

## Recent major changes

### May 2026 — gRPC migration + StartRootActivity (commit `b4d38bf`, `4665511`, `04cc63a`)
- Sample apps migrated from HTTP to gRPC for API→Backend
- Added `StartRootActivity` extension for independent traces in workers
- `BdcEnvironment.PRD_BATCH` renamed to `BTC` (alias accepted)
- Added `DebugTraceStateProcessor`
- `BDC_OTEL_SAMPLE_RATIO` env var
- Renamed API: `AddBDCTelemetry` → `AddBDCOtel`

### May 2026 — Python lib introduced
- Created `python/` directory with `bdc_otel` library
- Full parity with .NET (entry point, env vars, behavior per environment)
- Notification service sample apps
- 63 tests, 94% coverage

### April 2026 — Pyroscope removed, profiling on stand-by
- `IProfilingProvider` kept as abstraction
- `PyroscopeProfilingProvider` stub (no-op)

## Build commands

### .NET (always via Docker)
```bash
cd /home/karlipegomes/Documents/BDC/01-DEVOPS/LABS/bdctelemetryhelper/dotnet

# Build
docker run --rm -v $(pwd):/src -w /src mcr.microsoft.com/dotnet/sdk:8.0 dotnet build

# Test
docker run --rm -v $(pwd):/src -w /src mcr.microsoft.com/dotnet/sdk:8.0 dotnet test

# Pack (NuGet)
docker run --rm -v $(pwd):/src -w /src mcr.microsoft.com/dotnet/sdk:8.0 dotnet pack
```

### Python (always via Docker)
```bash
cd /home/karlipegomes/Documents/BDC/01-DEVOPS/LABS/bdctelemetryhelper/python

# Test
docker run --rm -v $(pwd):/app -w /app python:3.11-slim sh -c \
  "pip install -e '.[dev]' -q && pytest tests/ -v"
```

## Pending items

### .NET library
- [ ] Re-add Remote Debug Mode with safety controls
- [ ] Migrate profiling to OTel SDK native when Profiles signal reaches Beta/GA
- [ ] Add messaging instrumentation (Kafka, RabbitMQ)
- [ ] Evaluate declarative configuration (OTel SDK feature, stabilized early 2026)

### Python library
- [ ] Publish to internal PyPI registry (currently dev builds only)
- [ ] Verify metric naming consistency with .NET (Prometheus conversion)

### Sample apps
- [ ] Add Kafka/RabbitMQ message consumer/producer to sample apps
- [ ] Add Pyroscope dashboard (when profiling re-enabled)

### Infrastructure
- [ ] Replace Fluent Bit with OTel Collector filelog receiver (unified log pipeline)

## Related skills

- `bdc-telemetry-standard` — Lib API, env vars, behavior per environment, anti-patterns
- `dotnet-otel-patterns` — StartRootActivity, debug processor, advanced .NET patterns
- `python-otel-patterns` — gRPC aio, FastAPI, Python 3.11 vs 3.12
- `grpc-distributed-tracing` — Cross-language gRPC tracing
- `observability-principles` (steering) — Cross-cutting principles
- `dev-environment` (steering) — Docker build patterns

## Reference links

- Repo: `/home/karlipegomes/Documents/BDC/01-DEVOPS/LABS/bdctelemetryhelper`
- Sample apps: `bdctelemetryhelper/{dotnet,python}/example/`
- Dashboards: `bdctelemetryhelper/dashboards/`
- Tests docs: `bdctelemetryhelper/{dotnet,python}/TESTS.md`
- HOW-TO guides: `bdctelemetryhelper/{dotnet,python}/HOW-TO.md`
