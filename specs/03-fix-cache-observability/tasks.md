# Tasks: Fix Cache & Observability

- [x] T1: Replace `hash()` with `hashlib.sha256` in all data cache that uses hash (C1) — done 2026-06-14
- [x] T2: Remove LLM response cache from all 5 agents; keep inventory/costs/cluster_state/metrics cache (C2) — done 2026-06-14
- [x] T3: `logger.py` — conditional OTLP exporter based on `OTEL_EXPORTER_OTLP_ENDPOINT`, fallback console (O1) — done 2026-06-14 (previously done in otel-helper integration)
- [x] T4: `logger.py` — `JSONFormatter` captures extra record attributes (O2) — done 2026-06-14
- [x] T5: `logger.py` — `service.name` from `SERVICE_NAME` env (O3) — done 2026-06-14 (previously done in otel-helper integration)
- [x] T6: observability agent — `PROMETHEUS_URL` via env (O4) — done 2026-06-14
- [x] T7: Add `SERVICE_NAME` + `OTEL_EXPORTER_OTLP_ENDPOINT` per service in `docker-compose.yaml` (O3) — done 2026-06-14
- [x] T8: Replace `datetime.utcnow()` with `datetime.now(timezone.utc)` (D5) — done 2026-06-14
- [x] T9: Tests `tests/test_cache.py` (deterministic key) and `tests/test_logger.py` (extras in JSON) (depends on: T1,T4) — done 2026-06-14
- [x] T10: Build + tests via Docker (depends on: T9) — done 2026-06-14

## Suggested order
T1/T2 (cache), T3/T4/T5 (logger) in parallel; T6/T7/T8; T9 → T10.

## Notes
- Depends on spec 02 being done (unified agents) to apply consistently. Can be done together if 02 is already in progress.

## Status (2026-06-14)

**Completed**: All tasks (T1–T10). Cache fix (sha256), JSONFormatter with extras, PROMETHEUS_URL env, datetime fix, custom metrics, docs/METRICS.md created.

**Notes**:
- T3/T5 (OTel pipeline OTLP exporter, service.name) were already functional from earlier otel-helper integration work; confirmed working in this pass.
- Custom metrics exposed and documented in `docs/METRICS.md`.

**Deferred**: Nothing — spec fully complete.
