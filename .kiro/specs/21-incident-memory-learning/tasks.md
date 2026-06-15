# Tasks: Incident Memory & Learning

## Phase 1 — Storage & Models

- [ ] T1: Add PostgreSQL+pgvector container to docker-compose.yaml (image: `pgvector/pgvector:pg16`)
- [ ] T2: SQL migration script (`infra/postgres/init.sql`) — `kb_items` + `kb_provenance` tables, HNSW index, FTS index
- [ ] T3: Models in `src/core/kb/models.py`: `KbItem`, `KbDelta` (action enum), `KbProvenance`
- [ ] T4: `KbStore` (PG client) in `src/core/kb/store.py` — CRUD + similarity search + supersede detection

## Phase 2 — Extraction & Distillation Pipeline

- [ ] T5: `PIIRedactor` in `src/core/kb/redactor.py` — regex patterns for emails, AWS keys, tokens, IPs
- [ ] T6: `Extractor` in `src/core/kb/extractor.py` — Sonnet call, RCAResult → `list[KbDelta]`
- [ ] T7: `Enricher` in `src/core/kb/enricher.py` — Opus call, drafts + RCA → refined deltas
- [ ] T8: `Validator` in `src/core/kb/validator.py` — thresholds per type, decides auto/manual/discard

## Phase 3 — Embedding & RAG Injection

- [ ] T9: `Embedder` in `src/core/kb/embedder.py` — wraps Bedrock Titan or OpenAI text-embedding-3-small
- [ ] T10: `RagInjector` in `src/core/kb/rag.py` — query KB by symptom embedding, format `<similar_cases>` block
- [ ] T11: Wire RagInjector into `run_investigation` (before fan-out) — best-effort, fail-open

## Phase 4 — Approval & Budget

- [ ] T12: `BudgetGuard` in `src/core/kb/budget.py` — monthly cap, Redis counter
- [ ] T13: Endpoints `/kb/{id}/approve`, `/kb/{id}/reject`, `/kb` (list pending) in supervisor server
- [ ] T14: Distillation pipeline orchestrator `src/supervisor/distillation.py` — runs after investigation, async/fire-and-forget

## Phase 5 — Documentation & Tests

- [ ] T15: `docs/KNOWLEDGE-BASE.md` (architecture, approval workflow, metrics)
- [ ] T16: Update `docs/METRICS.md` with `aigent.kb.*` metrics
- [ ] T17: Tests by separate agent (≥80%): extractor, enricher, redactor, validator, store, RAG injection
- [ ] T18: Smoke test: run investigation → verify KB item created → second similar investigation → verify RAG hit

## Order

T1+T2 → T3 → T4 (storage stack)
T5 → T6+T7 → T8 (distillation pipeline)
T9 → T10 → T11 (RAG injection)
T12 → T13 → T14 (control plane)
T15+T16+T17+T18 (closing gate)

## Notes

- **Decision items NEVER auto-approve** — always pending_review, even if confidence > threshold.
- **PII redaction is mandatory** before any LLM call (extractor receives redacted RCA).
- **Distillation is fire-and-forget** — investigation already returned to user when distillation runs.
- **Budget exhaustion = skip learning, NOT fail investigation** — graceful degradation.
- **Embedding model**: prefer Bedrock Titan Embed (`amazon.titan-embed-text-v2:0`, 1024 dims) over OpenAI (avoid extra vendor); update vector dimension in schema accordingly.
