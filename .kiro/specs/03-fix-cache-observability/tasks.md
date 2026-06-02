# Tasks: Fix Cache & Observability

- [ ] T1: Trocar `hash()` por `hashlib.sha256` em todo cache de dados que use hash (C1)
- [ ] T2: Remover cache da resposta do LLM nos 5 agentes; manter cache de inventory/costs/cluster_state/metrics (C2)
- [ ] T3: `logger.py` — exporter OTLP condicional a `OTEL_EXPORTER_OTLP_ENDPOINT`, fallback console (O1)
- [ ] T4: `logger.py` — `JSONFormatter` captura atributos extra do record (O2)
- [ ] T5: `logger.py` — `service.name` de `SERVICE_NAME` env (O3)
- [ ] T6: observability agent — `PROMETHEUS_URL` via env (O4)
- [ ] T7: Adicionar `SERVICE_NAME` + `OTEL_EXPORTER_OTLP_ENDPOINT` por serviço no `docker-compose.yaml` (O3)
- [ ] T8: Substituir `datetime.utcnow()` por `datetime.now(timezone.utc)` (D5)
- [ ] T9: Testes `tests/test_cache.py` (key determinística) e `tests/test_logger.py` (extras no JSON) (depends on: T1,T4)
- [ ] T10: Build + testes via Docker (depends on: T9)

## Ordem sugerida
T1/T2 (cache), T3/T4/T5 (logger) em paralelo; T6/T7/T8; T9 → T10.

## Notas
- Depende de spec 02 estar feita (agentes unificados) para aplicar de forma consistente. Pode ser feita junto se 02 já estiver em andamento.
