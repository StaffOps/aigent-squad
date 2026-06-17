# Design: Fix Cache & Observability

## Cache (C1, C2)

### Princípio
Separar **dois tipos de cache** que hoje estão misturados:

| Tipo | Exemplo | Chave | Por quê |
|------|---------|-------|---------|
| Dados de infra | inventory, aws_costs, cluster_state, metrics | por-recurso (`"inventory"`, `"aws_costs"`) | caro, independe do usuário — **manter** |
| Resposta do LLM | `query:{hash}` | por-query | conversacional, depende de history/usuário — **remover** |

### Decisão
Remover o cache da resposta do `bedrock.invoke` nos agentes. O ganho de custo real está em não re-coletar inventory/costs a cada chamada (esses já têm cache próprio e continuam). A resposta do LLM por query causa bugs de contexto (C2) e economiza pouco.

Se no futuro fizer sentido cachear resposta, a key deve ser:
```python
import hashlib
raw = f"{user_id}:{session_id}:{input_text}:{history_digest}"
key = f"query:{hashlib.sha256(raw.encode()).hexdigest()}"
```

### Cache de dados (mantido) — key determinística
Onde houver hash, trocar `hash()` por `hashlib.sha256(...).hexdigest()`.

## Observabilidade (O1–O4)

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

### JSONFormatter — capturar extras

`logging` injeta as chaves de `extra=` como atributos do `record`. Capturar o que não é padrão:

```python
_STD = set(logging.makeLogRecord({}).__dict__.keys()) | {"message", "asctime"}

def format(self, record):
    log_data = { ...campos base... }
    for k, v in record.__dict__.items():
        if k not in _STD and not k.startswith("_"):
            log_data[k] = v
    ...
```

### observability agent — env

`self.prometheus_url = os.getenv("PROMETHEUS_URL", "http://prometheus.monitoring.svc.cluster.local:9090")`.

### compose
Adicionar por serviço:
```yaml
environment:
  - SERVICE_NAME=aws-agent          # (k8s-agent, finops-agent, ...)
  - OTEL_EXPORTER_OTLP_ENDPOINT=${OTEL_EXPORTER_OTLP_ENDPOINT:-}
```

## datetime (D5)
`datetime.utcnow()` → `datetime.now(timezone.utc)`. Os `.isoformat()` continuam válidos (passa a incluir offset; aceitável).

## Invariantes
- Cache de dados de infra preservado (TTLs inalterados).
- Sem OTLP endpoint, comportamento de dev (console) preservado.
- Formato de timestamp permanece ISO 8601.

## Dependências externas
- OTLP Collector (opcional, via env). Já existe `opentelemetry-exporter-otlp` no `requirements.txt`.

## Verificação
```bash
docker run --rm -v $(pwd):/app -w /app python:3.11-slim \
  sh -c "pip install -q -r requirements.txt pytest && pytest tests/test_cache.py tests/test_logger.py -v"
```
Alinha com `observability-principles.md` (App SDK → Collector) e `12-factor-app.md` (config via env).
