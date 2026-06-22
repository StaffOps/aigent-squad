# Tasks: Fix Cache & Observability

- [x] T1: Trocar `hash()` por `hashlib.sha256` em todo cache de dados que use hash (C1) — done 2026-06-14
- [x] T2: Remover cache da resposta do LLM nos 5 agentes; manter cache de inventory/costs/cluster_state/metrics (C2) — done 2026-06-14
- [x] T3: `logger.py` — exporter OTLP condicional a `OTEL_EXPORTER_OTLP_ENDPOINT`, fallback console (O1) — done 2026-06-14 (previously done in otel-helper integration)
- [x] T4: `logger.py` — `JSONFormatter` captura atributos extra do record (O2) — done 2026-06-14
- [x] T5: `logger.py` — `service.name` de `SERVICE_NAME` env (O3) — done 2026-06-14 (previously done in otel-helper integration)
- [x] T6: observability agent — `PROMETHEUS_URL` via env (O4) — done 2026-06-14
- [x] T7: Adicionar `SERVICE_NAME` + `OTEL_EXPORTER_OTLP_ENDPOINT` por serviço no `docker-compose.yaml` (O3) — done 2026-06-14
- [x] T8: Substituir `datetime.utcnow()` por `datetime.now(timezone.utc)` (D5) — done 2026-06-14
- [x] T9: Testes `tests/test_cache.py` (key determinística) e `tests/test_logger.py` (extras no JSON) (depends on: T1,T4) — done 2026-06-14
- [x] T10: Build + testes via Docker (depends on: T9) — done 2026-06-14

## Ordem sugerida
T1/T2 (cache), T3/T4/T5 (logger) em paralelo; T6/T7/T8; T9 → T10.

## Notas
- Depende de spec 02 estar feita (agentes unificados) para aplicar de forma consistente. Pode ser feita junto se 02 já estiver em andamento.

## Status (2026-06-14)

**Completed**: All tasks (T1–T10). Cache fix (sha256), JSONFormatter with extras, PROMETHEUS_URL env, datetime fix, custom metrics, docs/METRICS.md created.

**Notes**:
- T3/T5 (OTel pipeline OTLP exporter, service.name) were already functional from earlier otel-helper integration work; confirmed working in this pass.
- Custom metrics exposed and documented in `docs/METRICS.md`.

**Deferred**: Nothing — spec fully complete.
