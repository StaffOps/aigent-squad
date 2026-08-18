---
name: telemetry-standard
description: "<ORG> OTel Helper library standard for .NET and Python. Use when integrating telemetry into <ORG> apps, choosing between manual OTel SDK config vs the <ORG> libs, or designing new services that emit traces/metrics/logs. Covers the corporate observability standard, lib API, env vars, behavior per environment."
keywords: [telemetry, otel, python, net, sdk]
---
# <ORG> OTel Helper — Corporate Observability Standard

The libraries `<ORG>OtelHelper` (.NET) and `<org>_otel` (Python) abstract OpenTelemetry complexity. **One call configures everything** — apps shouldn't manually configure OTel.

## Goal

Every <ORG> app emits standardized telemetry with zero effort from app teams:
- Distributed traces
- Metrics (RED + business + runtime)
- Structured logs (with trace correlation)
- Profiles (when re-enabled)
- Context propagation (HTTP, gRPC, Kafka)

Apps that don't comply are not production-ready.

## Architecture

```
[ Application ]
        ↓ OTLP gRPC :4317
[ OpenTelemetry Collector (cluster + gateway + OTLP) ]
        ↓
 ┌──────────────┬──────────────┬──────────────┬──────────────┐
 │ Tempo        │ VictoriaM.   │ Loki         │ Pyroscope    │
 │ (traces)     │ (metrics)    │ (logs)       │ (profiles)   │
 └──────────────┴──────────────┴──────────────┴──────────────┘
```

## Lib repositories

Monorepo: `/home/karlipegomes/Documents/<ORG>/01-DEVOPS/LABS/<org>telemetryhelper`

```
<org>telemetryhelper/
├── README.md
├── .gitlab-ci.yml
├── dashboards/                    # Shared Grafana dashboards
├── dotnet/                        # .NET lib
│   ├── <ORG>OtelHelper/
│   ├── <ORG>OtelHelper.Tests/       # 61 tests, 90% coverage
│   └── example/                   # Sample apps (api/backend/process)
└── python/                        # Python lib
    ├── <org>_otel/
    ├── tests/                     # 63 tests, 94% coverage
    └── example/                   # Notification service
```

## Usage — .NET

```csharp
// Minimal — endpoints from OTEL_EXPORTER_OTLP_ENDPOINT env var
services.Add<ORG>Otel();

// With overrides
services.Add<ORG>Otel(opts =>
{
    opts.ServiceName = "checkout-api";
    opts.ResourceAttributes = new Dictionary<string, object>
    {
        ["app.component"] = "api-gateway"
    };
    opts.AdditionalActivitySources = new List<string> { "MyApp.Orders" };
});
```

Note: API was renamed from `Add<ORG>Telemetry` to `Add<ORG>Otel` in May 2026.

## Usage — Python

```python
from <org>_otel import setup_telemetry

# Call inside main(), not at module level (avoids silent failures)
setup_telemetry()

# Helpers
from <org>_otel import get_tracer, get_meter, start_root_span

tracer = get_tracer(__name__)
meter = get_meter(__name__)

# For workers: independent traces per iteration
with start_root_span("queue-consume") as span:
    ...
```

## Configuration (both libs)

| Property | Type | Default | Source |
|----------|------|---------|--------|
| ServiceName | string | `my-service` | `SERVICE_NAME` env > `OTEL_SERVICE_NAME` env > code |
| Environment | enum | LOCAL | `ENVIRONMENT` env |
| OtelCollectorEndpoint | string | derived | `OTEL_EXPORTER_OTLP_ENDPOINT` + port 4317 |
| EnableProfiling | bool | false | `<ORG>_OTEL_PROFILING_ENABLED` (currently no-op) |
| DebugLevel | bool | false | `<ORG>_OTEL_DEBUG_LEVEL` |
| ExtraInstrumentation | string | `SQL` | `<ORG>_OTEL_EXTRA_INSTRUMENTATION` (e.g. `SQL,AWS,REDIS`) |
| SampleRatio | float | 1.0 | `<ORG>_OTEL_SAMPLE_RATIO` (0.0-1.0) |

## Environment enum (.NET) / values (Python)

```
LOCAL → log DEBUG, 100% sampling
DEV   → log INFO,  100% sampling
HML   → log INFO,  100% sampling
PRD   → log WARN,  100% sampling (tail sampling at Collector)
BTC   → log WARN,  100% sampling (renamed from PRD_BATCH/PRD-BATCH)
```

Aliases: `PRD-BATCH`, `PRD_BATCH` → maps to `BTC`.

When `<ORG>_OTEL_DEBUG_LEVEL=true`: log Debug, profiling force-enabled, all extra instrumentations enabled. Overrides any environment.

## Auto-configured instrumentation

### .NET (mandatory)
- ASP.NET Core
- HTTP client
- gRPC client (auto via OpenTelemetry.Instrumentation.GrpcNetClient 1.15.1-beta.1)
- .NET runtime metrics

### .NET (conditional via `<ORG>_OTEL_EXTRA_INSTRUMENTATION`)
- SqlClient (`SQL` — default enabled)
- AWS SDK (`AWS` — opt-in)

### Python (all instrumentations as core deps)
- FastAPI
- HTTPX, requests
- gRPC (aio variants — `GrpcAioInstrumentorClient/Server`)
- SQLAlchemy
- Redis
- Botocore (AWS SDK)
- System metrics (CPU, memory, GC, network)

## Sampling strategy (CRITICAL)

- **SDK side**: `AlwaysOnSampler` (sends 100%)
- **Collector side**: tail-based sampling decides what to keep

Why NOT head sampling in SDK:
- Drops error traces before they can be evaluated
- Can't make decisions based on outcome (latency, errors)
- Tail sampling at Collector evaluates after execution

`<ORG>_OTEL_SAMPLE_RATIO < 1.0` enables `TraceIdRatioBasedSampler` — use only if there's a specific reason.

## Logs — native ILogger integration (.NET) / Logger (Python)

### .NET
```csharp
builder.Logging.AddOpenTelemetry(logging =>
{
    logging.IncludeFormattedMessage = true;
    logging.IncludeScopes = true;
    logging.AddOtlpExporter();
});
// + ParseStateValues = true → structured log placeholders become attributes
// + Filter Microsoft.* / System.Net.Http.* to Error in non-debug
```

### Anti-patterns (REMOVED from lib)
- ❌ Custom `ILogEnricher` with manual JSON serialization
- ❌ `ActivityLogEnricher` manually reading Activity.Current
- ❌ Console-only logging without OTLP export

Trace correlation (traceId/spanId) automatic via `Activity.Current` (Python: `OpenTelemetry-Logs SDK`).

## Resource attributes — separation of concerns

### SDK responsibility
- `service.name` ONLY

### Collector responsibility (k8sattributesprocessor + transform)
- `service.version`
- `deployment.environment`
- `cloud.provider`, `cloud.region`
- `k8s.namespace.name`, `k8s.pod.name`, `k8s.deployment.name`, `k8s.statefulset.name`
- `eks_cluster`, `cluster`

## Exemplars

```csharp
.SetExemplarFilter(ExemplarFilterType.TraceBased)
```

Links metrics to traces. Click on metric spike in Grafana → jump to exact trace. Already enabled by lib.

## Sample apps in monorepo

### .NET
- `dotnet-api/` — Minimal API, 13 endpoints, demonstrating distributed tracing patterns (retry, cache, baggage, events, parallel, queue, circuit breaker)
- `dotnet-backend/` — gRPC server, 5 RPCs (ProcessOrder, CancelOrder, SlowOperation, UnstableOperation, ReadBaggage)
- `dotnet-process/` — 4 background workers (ApiHealthWorker, HeavyProcessWorker, QueueConsumerWorker, ScheduledJobWorker)

### Python (notification service)
- `python-api/` — FastAPI :8000, calls backend via gRPC
- `python-backend/` — gRPC :50051 (SendNotification, GetStatus, RenderTemplate)
- `python-process/` — Workers (QueueConsumer, RetryWorker, CleanupWorker, HealthChecker)

## Versioning

| Lib | Format | Example dev | Example release |
|-----|--------|-------------|-----------------|
| .NET | SemVer | `0.1.0-dev-<short_sha>` | `1.0.0` |
| Python | PEP 440 | `0.1.0.dev<pipeline_id>` | `1.0.0` |

See `ci-cd-conventions.md` for details.

## CI/CD pipeline (4 stages)

```
unit-test → build-dev → demo → build (manual)
```

- `demo` stage: ephemeral end-to-end (Docker run, curl endpoints, query Tempo for traces)
- Dual-arch builds (amd64 + arm64) in `demo` and `build` stages

## Profiling status

**Stand-by**. Pyroscope SDK packages removed (compatibility issues with OTel 1.15.3). The `IProfilingProvider` interface and stub `PyroscopeProfilingProvider` remain for future migration.

Will be re-enabled when:
1. Pyroscope packages updated, OR
2. OTel native Profiles signal stabilizes (currently Alpha, March 2026)

## Anti-patterns (NEVER do)

- ❌ Direct vendor SDK usage (Datadog APM, New Relic agent)
- ❌ Custom OTel SDK setup bypassing the lib
- ❌ Direct export to backends (must go through Collector)
- ❌ Logging without trace correlation
- ❌ Head sampling in SDK
- ❌ High-cardinality labels (UserId, JobId)
- ❌ Custom JSON log serialization
- ❌ Hardcoded OTLP endpoints (use env var)
- ❌ Using deprecated `AddOpenTelemetryTracing()` / `AddOpenTelemetryMetrics()`

## Related skills

- `dotnet-otel-patterns` — Advanced .NET patterns (StartRootActivity, debug processor)
- `python-otel-patterns` — Python-specific patterns (gRPC aio, propagation)
- `grpc-distributed-tracing` — Cross-language gRPC tracing patterns
- `observability-principles` (steering) — Cross-cutting principles
