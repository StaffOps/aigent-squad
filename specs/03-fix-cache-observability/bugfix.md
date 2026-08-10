# Bugfix: Fix Cache & Observability

**Spec**: `03-fix-cache-observability`
**Severity**: 🟠 High (cache) / 🟡 Medium (observability)
**Findings**: C1, C2, O1, O2, O3, O4, D5 (see `../AUDIT.md`)

---

## C1 — Native `hash()` in cache key

**Current**: `cache_key = f"query:{hash(input_text)}"` in all agents. `hash()` for str is randomized per process (`PYTHONHASHSEED`), so across replicas/restarts the cache never hits.

**Expected**: deterministic and stable key across processes.

**Fix**: `hashlib.sha256(input_text.encode()).hexdigest()`.

**Unchanged**: namespaces and TTLs per agent.

---

## C2 — Cache key ignores identity and context

**Current**: the key only considers `input_text`. Follow-ups ("yes", "more") collide with previous responses; different users share responses.

**Expected**: conversational responses don't leak across users/sessions; follow-ups don't return stale cache.

**Fix (design decision)**: the final LLM response **should not be cached by query** in a conversational agent. Keep cache only for **infra data** (inventory, costs, cluster_state, metrics) that are expensive and user-independent. Remove the `bedrock.invoke` response cache or, if kept, compose the key with `session_id` + history hash. Preference: remove response cache; keep data cache.

**Unchanged**: cache of inventory/costs/cluster_state/metrics (those are per-resource, not per-user).

---

## O1 — `ConsoleSpanExporter` hardcoded

**Current**: `logger.py` exports spans only to console.

**Expected**: exports via OTLP when `OTEL_EXPORTER_OTLP_ENDPOINT` is set; falls back to console if absent (dev).

**Fix**: use `OTLPSpanExporter` conditional on the env var.

---

## O2 — `JSONFormatter` loses extra fields

**Current**: reads `record.extra` (doesn't exist). Structured context (`agent_id`, `duration_ms`…) disappears from the log.

**Expected**: fields passed via `logger.info(msg, extra={...})` appear in the JSON.

**Fix**: iterate attributes of `record` that are not standard `LogRecord` and merge them into `log_data`.

---

## O3 — Fixed `service.name`

**Current**: `"agent-squad"` for all services.

**Expected**: per-service name, from env (`SERVICE_NAME`/`OTEL_SERVICE_NAME`), default `agent-squad`.

**Fix**: `Resource.create({"service.name": os.getenv("SERVICE_NAME", "agent-squad")})` + set the env per service in compose.

---

## O4 — `PROMETHEUS_URL` ignored

**Current**: observability agent hardcodes the URL.

**Expected**: reads from `settings`/env (12-factor III).

**Fix**: `self.prometheus_url = os.getenv("PROMETHEUS_URL", "<default cluster>")`.

---

## D5 — `datetime.utcnow()` deprecated

**Current**: used in `state_store.py`, agents, supervisor.

**Expected**: `datetime.now(timezone.utc)`.

**Fix**: mechanical substitution, preserving ISO timestamp format.

---

## Acceptance criteria

- [ ] Cache key is deterministic between two distinct processes (test).
- [ ] LLM response is not shared between different `user_id`s (or response cache removed).
- [ ] Log JSON includes `extra` fields (formatter test).
- [ ] With `OTEL_EXPORTER_OTLP_ENDPOINT` set, spans go to OTLP; without it, console.
- [ ] `SERVICE_NAME` reflects each service in the trace.
- [ ] `PROMETHEUS_URL` respected.
- [ ] No `datetime.utcnow()` in the code.
