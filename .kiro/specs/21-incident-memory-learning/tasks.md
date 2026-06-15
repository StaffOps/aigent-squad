# Tasks: Incident Memory & Learning

## Phase 1 — Storage & Models

- [x] T1: Add PostgreSQL+pgvector container to docker-compose.yaml (image: `pgvector/pgvector:pg16`) — done 2026-06-14
- [x] T2: SQL migration script (`infra/postgres/init.sql`) — `kb_items` + `kb_provenance` tables, HNSW index, FTS index — done 2026-06-14
- [x] T3: Models in `src/core/kb/models.py`: `KbItem`, `KbDelta` (action enum), `KbProvenance` — done 2026-06-14
- [x] T4: `KbStore` (PG client) in `src/core/kb/store.py` — CRUD + similarity search + supersede detection — done 2026-06-14

## Phase 2 — Extraction & Distillation Pipeline

- [x] T5: `PIIRedactor` in `src/core/kb/redactor.py` — regex patterns for emails, AWS keys, tokens, IPs — done 2026-06-14
- [x] T6: `Extractor` in `src/core/kb/extractor.py` — Sonnet call, RCAResult → `list[KbDelta]` — done 2026-06-14
- [x] T7: `Enricher` in `src/core/kb/enricher.py` — Opus call, drafts + RCA → refined deltas — done 2026-06-14 (Sonnet-only; Opus deferred — BEDROCK_OPUS_MODEL_ID env not set by default)
- [x] T8: `Validator` in `src/core/kb/validator.py` — thresholds per type, decides auto/manual/discard — done 2026-06-14

## Phase 3 — Embedding & RAG Injection

- [x] T9: `Embedder` in `src/core/kb/embedder.py` — wraps Bedrock Titan or OpenAI text-embedding-3-small — done 2026-06-14
- [x] T10: `RagInjector` in `src/core/kb/rag.py` — query KB by symptom embedding, format `<similar_cases>` block — done 2026-06-14
- [x] T11: Wire RagInjector into `run_investigation` (before fan-out) — best-effort, fail-open — done 2026-06-14

## Phase 4 — Approval & Budget

- [x] T12: `BudgetGuard` in `src/core/kb/budget.py` — monthly cap, Redis counter — done 2026-06-14
- [x] T13: Endpoints `/kb/{id}/approve`, `/kb/{id}/reject`, `/kb` (list pending) in supervisor server — done 2026-06-14 (HTTP endpoints only; Slack approval flow NOT done)
- [x] T14: Distillation pipeline orchestrator `src/supervisor/distillation.py` — runs after investigation, async/fire-and-forget — done 2026-06-14

## Phase 5 — Documentation & Tests

- [x] T15: `docs/KNOWLEDGE-BASE.md` (architecture, approval workflow, metrics) — done 2026-06-14
- [x] T16: Update `docs/METRICS.md` with `aigent.kb.*` metrics — done 2026-06-14
- [x] T17: Tests by separate agent (≥80%): extractor, enricher, redactor, validator, store, RAG injection — done 2026-06-14
- [x] T18: Smoke test: run investigation → verify KB item created → second similar investigation → verify RAG hit — done 2026-06-14

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

## Status (2026-06-14)

**Completed**: All phases (1–4) and documentation/tests (Phase 5). Full incident memory pipeline: storage (pgvector), extraction (Sonnet), enrichment, validation, embedding (Titan), RAG injection, budget guard, approval endpoints, distillation orchestrator.

**Notable implementation details**:
- T7 (Enricher): Uses Sonnet only. Opus enrichment deferred — `BEDROCK_OPUS_MODEL_ID` env var not set by default. System degrades gracefully (Sonnet enrichment still runs).
- T13 (Approval endpoints): HTTP endpoints (`/kb/{id}/approve`, `/kb/{id}/reject`, `/kb`) implemented. Slack approval flow NOT implemented (would require webhook/bot integration).
- T5 (PII Redactor): Implemented with simple regex patterns (emails, AWS keys, tokens, IPs). Advanced NER-based redaction deferred.

**Deferred**:
- Opus enricher activation (awaits `BEDROCK_OPUS_MODEL_ID` env configuration)
- Slack-based approval workflow (HTTP-only for now)
- Advanced PII redaction (NER-based; current regex is sufficient for MVP)
