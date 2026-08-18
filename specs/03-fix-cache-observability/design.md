# Design: Fix Cache & Observability

## Cache (C1, C2)

### Principle
Separate **two types of cache** that are currently mixed:

| Type | Example | Key | Why |
|------|---------|-----|-----|
| Infra data | inventory, aws_costs, cluster_state, metrics | per-resource (`"inventory"`, `"aws_costs"`) | expensive, user-independent — **keep** |
| LLM response | `query:{hash}` | per-query | conversational, depends on history/user — **remove** |

### Decision
Remove the `bedrock.invoke` response cache from agents. The real cost savings comes from not re-collecting inventory/costs on every call (those already have their own cache and continue). LLM response-per-query cache causes context bugs (C2) and saves little.

If in the future response caching makes sense, the key should be:
```python
import hashlib
raw = f"{user_id}:{session_id}:{input_text}:{history_digest}"
key = f"query:{hashlib.sha256(raw.encode()).hexdigest()}"
```

### Data cache (kept) — deterministic key
Where hash is used, replace `hash()` with `hashlib.sha256(...).hexdigest()`.

## Observability (O1–O4)

### logger.py — refactor

```python
import os
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import ConsoleSpanExporter

service_name = os.getenv("SERVICE_NAME", "agent-squad")
resource = Resource.create({"service.name": service_name})
trace.set_tracer_provider(TracerProvider(resource=resource))

otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
exporter = OTLPSpanExporter() if otlp_endpoint else ConsoleSpanExporter()
trace.get_tracer_provider().add_span_processor(BatchSpanProcessor(exporter))
```

### JSONFormatter — capture extras

`logging` injects `extra=` keys as `record` attributes. Capture what's not standard:

```python
_STD = set(logging.makeLogRecord({}).__dict__.keys()) | {"message", "asctime"}

def format(self, record):
    log_data = { ...base fields... }
    for k, v in record.__dict__.items():
        if k not in _STD and not k.startswith("_"):
            log_data[k] = v
    ...
```

### observability agent — env

`self.prometheus_url = os.getenv("PROMETHEUS_URL", "http://prometheus.monitoring.svc.cluster.local:9090")`.

### compose
Add per service:
```yaml
environment:
  - SERVICE_NAME=aws-agent          # (k8s-agent, finops-agent, ...)
  - OTEL_EXPORTER_OTLP_ENDPOINT=${OTEL_EXPORTER_OTLP_ENDPOINT:-}
```

## datetime (D5)
`datetime.utcnow()` → `datetime.now(timezone.utc)`. The `.isoformat()` calls remain valid (will now include offset; acceptable).

## Invariants
- Infra data cache preserved (TTLs unchanged).
- Without OTLP endpoint, dev behavior (console) preserved.
- Timestamp format remains ISO 8601.

## External dependencies
- OTLP Collector (optional, via env). `opentelemetry-exporter-otlp` already in `requirements.txt`.

## Verification
```bash
docker run --rm -v $(pwd):/app -w /app python:3.11-slim \
  sh -c "pip install -q -r requirements.txt pytest && pytest tests/test_cache.py tests/test_logger.py -v"
```
Aligns with `observability-principles.md` (App SDK → Collector) and `12-factor-app.md` (config via env).
