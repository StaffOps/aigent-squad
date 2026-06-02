# Bugfix: Fix Cache & Observability

**Spec**: `03-fix-cache-observability`
**Severidade**: 🟠 High (cache) / 🟡 Medium (observabilidade)
**Achados**: C1, C2, O1, O2, O3, O4, D5 (ver `../AUDIT.md`)

---

## C1 — `hash()` nativo na cache key

**Current**: `cache_key = f"query:{hash(input_text)}"` em todos os agentes. `hash()` de str é randomizado por processo (`PYTHONHASHSEED`), então entre réplicas/restarts o cache nunca acerta.

**Expected**: chave determinística e estável entre processos.

**Fix**: `hashlib.sha256(input_text.encode()).hexdigest()`.

**Unchanged**: namespaces e TTLs por agente.

---

## C2 — Cache key ignora identidade e contexto

**Current**: a key só considera `input_text`. Follow-ups ("yes", "more") colidem com respostas anteriores; usuários diferentes compartilham resposta.

**Expected**: respostas conversacionais não vazam entre usuários/sessões; follow-ups não retornam cache equivocado.

**Fix (decisão de design)**: a resposta final do LLM **não deve ser cacheada por query** num agente conversacional. Manter cache apenas para **dados de infra** (inventory, costs, cluster_state, metrics) que são caros e independem do usuário. Remover o cache da resposta do `bedrock.invoke` ou, se mantido, compor a key com `session_id` + hash do histórico. Preferência: remover o cache de resposta; manter cache de dados.

**Unchanged**: cache de inventory/costs/cluster_state/metrics (esses são por-recurso, não por-usuário).

---

## O1 — `ConsoleSpanExporter` hardcoded

**Current**: `logger.py` exporta spans só para console.

**Expected**: exporta via OTLP quando `OTEL_EXPORTER_OTLP_ENDPOINT` estiver setado; cai para console se ausente (dev).

**Fix**: usar `OTLPSpanExporter` condicional à env var.

---

## O2 — `JSONFormatter` perde os campos extras

**Current**: lê `record.extra` (não existe). Contexto estruturado (`agent_id`, `duration_ms`…) some do log.

**Expected**: campos passados via `logger.info(msg, extra={...})` aparecem no JSON.

**Fix**: iterar atributos do `record` que não são padrão do `LogRecord` e mesclá-los no `log_data`.

---

## O3 — `service.name` fixo

**Current**: `"agent-squad"` para todos os serviços.

**Expected**: nome por serviço, de env (`SERVICE_NAME`/`OTEL_SERVICE_NAME`), default `agent-squad`.

**Fix**: `Resource.create({"service.name": os.getenv("SERVICE_NAME", "agent-squad")})` + setar a env por serviço no compose.

---

## O4 — `PROMETHEUS_URL` ignorado

**Current**: observability agent hardcoda a URL.

**Expected**: lê de `settings`/env (12-factor III).

**Fix**: `self.prometheus_url = os.getenv("PROMETHEUS_URL", "<default cluster>")`.

---

## D5 — `datetime.utcnow()` deprecado

**Current**: usado em `state_store.py`, agentes, supervisor.

**Expected**: `datetime.now(timezone.utc)`.

**Fix**: substituição mecânica, preservando formato ISO dos timestamps.

---

## Critérios de aceite

- [ ] Cache key é determinística entre dois processos distintos (teste).
- [ ] Resposta do LLM não é compartilhada entre `user_id` diferentes (ou cache de resposta removido).
- [ ] Log JSON inclui os campos de `extra` (teste de formatter).
- [ ] Com `OTEL_EXPORTER_OTLP_ENDPOINT` setado, spans vão para OTLP; sem ele, console.
- [ ] `SERVICE_NAME` reflete cada serviço no trace.
- [ ] `PROMETHEUS_URL` respeitada.
- [ ] Sem `datetime.utcnow()` no código.
