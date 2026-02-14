# Observability & Error Handling - Agent Squad v2.0

**Date**: 2026-02-14  
**Status**: ? Implemented

## ? Implemented Improvements

### 1. **Structured JSON Logging**

All logs are emitted in JSON format for easy parsing and analysis.

#### Log Format
```json
{
  "timestamp": "2026-02-14T14:00:00.000Z",
  "level": "INFO",
  "logger": "agent-squad",
  "message": "Request received",
  "module": "agent",
  "function": "process_request",
  "line": 42,
  "agent_id": "aws",
  "user_id": "user123",
  "session_id": "session456",
  "input_length": 25,
  "trace_id": "a1b2c3d4e5f6...",
  "span_id": "1234567890ab..."
}
```

#### Standard Fields
- `timestamp`: ISO 8601 UTC
- `level`: DEBUG, INFO, WARNING, ERROR, CRITICAL
- `logger`: Logger name
- `message`: Human-readable message
- `module`, `function`, `line`: Code location
- `trace_id`, `span_id`: OpenTelemetry context (when available)

---

### 2. **OpenTelemetry Tracing**

Complete distributed tracing in all components.

#### Created Spans

**Supervisor**:
- `supervisor.process_request` - Complete request
- `classifier.classify` - Intent classification
- `agent.http_call` - HTTP call to agent

**Agents**:
- `{agent_id}_agent.process_request` - Complete request
- `{agent_id}_agent.get_inventory` - Data fetch
- `{agent_id}_agent.bedrock_invoke` - Bedrock call

**Bedrock Client**:
- Retry and throttling logs
- Token metrics (input/output)

#### Attributes
Each span includes:
- `agent_id`
- `user_id`
- `session_id`
- `input_length`
- `response_length`
- `duration_ms`

---

### 3. **Error Handling**

#### Bedrock Client
- ? Automatic retry (3 attempts)
- ? Exponential backoff
- ? Throttling handling
- ? Detailed error logs

```python
# Errors with retry:
- ThrottlingException
- ServiceUnavailableException
- InternalServerException

# Errors without retry:
- ValidationException
- AccessDeniedException
```

#### Supervisor
- ? Configurable timeout (30s total, 5s connect, 25s read)
- ? HTTP retry (2 attempts)
- ? Timeout handling
- ? HTTP error handling

#### Agents
- ? Input validation (not empty, max 10k chars)
- ? Error logging with context
- ? Graceful degradation

---

### 4. **Input Validation**

All agents validate:
```python
# Not empty
if not input_text or not input_text.strip():
    raise ValueError("Input text cannot be empty")

# Maximum size
if len(input_text) > 10000:
    raise ValueError("Input text too long")
```

---

## ? OpenTelemetry Configuration

### Current Exporter (Console)
```python
# src/core/logger.py
span_processor = BatchSpanProcessor(ConsoleSpanExporter())
```

### Configure OTLP Collector (Future)
```python
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

otlp_exporter = OTLPSpanExporter(
    endpoint="http://otel-collector:4317",
    insecure=True
)
span_processor = BatchSpanProcessor(otlp_exporter)
trace.get_tracer_provider().add_span_processor(span_processor)
```

### Environment Variables
```bash
# OpenTelemetry
OTEL_SERVICE_NAME=agent-squad
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_EXPORTER_OTLP_INSECURE=true
OTEL_TRACES_SAMPLER=always_on
```

---

## ? Log Examples

### Request Received
```json
{
  "timestamp": "2026-02-14T14:00:00.000Z",
  "level": "INFO",
  "message": "Request received",
  "agent_id": "aws",
  "user_id": "user123",
  "session_id": "session456",
  "input_length": 25
}
```

### Classification
```json
{
  "timestamp": "2026-02-14T14:00:01.000Z",
  "level": "INFO",
  "message": "Intent classified",
  "selected_agent": "aws",
  "confidence": 0.95,
  "reasoning": "User asking about EC2 instances"
}
```

### Bedrock Invocation
```json
{
  "timestamp": "2026-02-14T14:00:02.000Z",
  "level": "INFO",
  "message": "Bedrock invocation successful",
  "model_id": "anthropic.claude-3-5-sonnet-20240620-v1:0",
  "input_tokens": 1250,
  "output_tokens": 450
}
```

### Response Sent
```json
{
  "timestamp": "2026-02-14T14:00:03.000Z",
  "level": "INFO",
  "message": "Response sent",
  "agent_id": "aws",
  "user_id": "user123",
  "session_id": "session456",
  "response_length": 450,
  "duration_ms": 3250
}
```

### Error
```json
{
  "timestamp": "2026-02-14T14:00:04.000Z",
  "level": "ERROR",
  "message": "Error in aws",
  "agent_id": "aws",
  "error_type": "ValidationError",
  "error_message": "Input text cannot be empty",
  "user_id": "user123",
  "session_id": "session456",
  "exception": "Traceback..."
}
```

---

## ? How to Use

### View Logs (Local)
```bash
# JSON logs on stdout
docker compose logs -f supervisor | jq .

# Filter by level
docker compose logs -f supervisor | jq 'select(.level=="ERROR")'

# Filter by agent
docker compose logs -f | jq 'select(.agent_id=="aws")'
```

### Integrate with Collector (Production)

1. **Deploy OTEL Collector**:
```yaml
# k8s_manifests/otel-collector.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: otel-collector-config
data:
  config.yaml: |
    receivers:
      otlp:
        protocols:
          grpc:
            endpoint: 0.0.0.0:4317
    
    exporters:
      logging:
        loglevel: debug
      otlp:
        endpoint: tempo:4317
    
    service:
      pipelines:
        traces:
          receivers: [otlp]
          exporters: [logging, otlp]
```

2. **Update agents**:
```python
# src/core/logger.py
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

otlp_exporter = OTLPSpanExporter(
    endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317"),
    insecure=True
)
```

3. **Visualize in Grafana/Tempo**

---

## ? Implementation Checklist

- [x] Structured JSON logger
- [x] OpenTelemetry tracing
- [x] Error handling in Bedrock (retry + backoff)
- [x] Timeout handling in Supervisor
- [x] Input validation in agents
- [x] Logging in all components
- [x] FastAPI instrumentation
- [x] HTTPX instrumentation
- [ ] Configure OTLP collector (production)
- [ ] Integrate with Grafana/Tempo
- [ ] Add Prometheus metrics

---

**Version**: 2.0  
**Status**: ? Ready for testing
