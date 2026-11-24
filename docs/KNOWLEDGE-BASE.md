# Knowledge Base & Learning

The system learns from each completed RCA investigation. Knowledge persists in PostgreSQL+pgvector and is reused via RAG injection on new investigations.

## Architecture

```
Investigation finishes (mode=investigate)
        │
        ▼
asyncio.create_task(distill_rca(rca))   ← fire-and-forget
        │
        ▼
┌───────────────────────────────────────────────┐
│         Distillation Pipeline                  │
│  1. Skip if confidence='baixa'                 │
│  2. Skip if monthly budget exhausted ($50)     │
│  3. Extractor (Sonnet)  → list[KbDelta]        │
│  4. Enricher (Sonnet)   → refined deltas       │
│  5. Validator           → status decision      │
│  6. Embedder (Titan v2) → 1024-dim vector      │
│  7. KbStore.insert()    → Postgres+pgvector    │
└───────────────────────────────────────────────┘

New investigation starts
        │
        ▼
inject_similar_cases(symptom)
   ├─ embed(symptom) → 1024-dim
   ├─ KbStore.search_similar(top_k=3, threshold=0.75)
   └─ format <similar_cases> XML block
        │
        ▼
Injected as PRIOR in RCA synthesizer prompt
```

## Item types & thresholds

| Type | Auto-approve threshold | Description |
|------|:----------------------:|-------------|
| `troubleshooting` | 0.85 | Symptom→diagnosis→fix patterns |
| `pattern` | 0.90 | Recurring scenarios |
| `infrastructure` | 0.80 | Factual config (URLs, hosts, settings) |
| `decision` | **never auto** | Architectural choices — always pending_review |

Items below 0.5 confidence are rejected (status: `rejected`).
Items between 0.5 and threshold go to `pending_review`.

## API

| Endpoint | Method | Purpose |
|----------|:------:|---------|
| `/kb/pending` | GET | List items awaiting human review |
| `/kb/{id}/approve` | POST | Move pending → active |
| `/kb/{id}/reject` | POST | Move pending → rejected |

All endpoints require `X-Internal-Token` header. With `KB_ENABLED=false` they answer **503** (`Knowledge base is disabled (KB_ENABLED=false)`), after the token check.

## RAG injection

Before each investigation's RCA synthesis, the symptom is embedded and the top-3 similar KB items (similarity ≥ 0.75) are injected into the synthesizer prompt as `<similar_cases>` XML block. The LLM is instructed to treat these as **priors**, not authoritative answers.

If the embedding service or DB is unavailable, RAG is skipped (fail-open).

## PII redaction

All RCA content passes through `PIIRedactor` BEFORE reaching the LLM (extractor/enricher) and BEFORE persisting:

- Emails → `<redacted:email>`
- AWS access keys (AKIA*, ASIA*) → `<redacted:aws-key>`
- GitHub PAT (`ghp_*`) → `<redacted:github-pat>`
- GitLab PAT (`glpat-*`) → `<redacted:gitlab-pat>`
- Bearer tokens → `Bearer <redacted:token>`
- OpenAI keys (`sk-*`) → `<redacted:openai-key>`

Patterns are in `src/core/kb/redactor.py`. Add new patterns there.

## Cost & budget

| Component | Cost per distillation |
|-----------|----------------------|
| Extractor (Sonnet) | ~$0.03 |
| Enricher (Sonnet, default — Opus optional) | ~$0.03 |
| Embedding (Titan v2) | ~$0.001 |
| Postgres (container) | $0 (local) |
| **Total** | **~$0.06–0.27** |

Default monthly budget: **$50**. Override via `KB_MONTHLY_BUDGET_USD` env var.

When budget is exhausted: distillation is skipped (logged warning); investigation still works (RAG injection continues using existing KB).

## Database

- Container: `pgvector/pgvector:pg16`
- Schema: `infra/postgres/init.sql` (kb_items + kb_provenance + HNSW index)
- Connection: `POSTGRES_*` env vars (host, port, user, password, db)
- Provenance: each new item gets a `kb_provenance` row (source investigation id, confidence,
  extractor and enricher model) in the same transaction, when the item comes from an
  investigation with a UUID id. The id is also kept in `metadata.investigation_id`.

## Retention (F-029)

| Items | Deleted when | Setting |
|-------|--------------|---------|
| `rejected`, `superseded` | N days after the last status change (`updated_at`) | `KB_REJECTED_RETENTION_DAYS` (default `30`) |
| `active`, `pending_review` | N days after creation (`created_at`), only if set | `KB_ITEM_MAX_AGE_DAYS` (default unset = keep) |

There is no cron. The sweep runs when the supervisor connects to the KB and then at most once
per `KB_RETENTION_INTERVAL_SECONDS` (default `3600`), started in the background by KB reads
and writes. Each rule deletes at most `KB_RETENTION_BATCH_SIZE` x `KB_RETENTION_MAX_BATCHES`
rows per sweep (defaults `500` x `10`), one transaction per batch, `FOR UPDATE SKIP LOCKED` so
replicas do not fight over rows. Provenance rows go with their item (`ON DELETE CASCADE`). The
sweep is fail-open: an error is logged and counted in `aigent.kb.retention.errors`; deletions
are counted in `aigent.kb.retention.deleted{rule}`.

## Running locally

```bash
docker compose up -d postgres
docker compose logs postgres  # confirm "database system is ready"

# Verify schema
docker compose exec postgres psql -U aigent -d aigent_kb -c "\dt"
# Should show: kb_items, kb_provenance

# Inspect items
docker compose exec postgres psql -U aigent -d aigent_kb -c "SELECT id, type, status, title FROM kb_items ORDER BY created_at DESC LIMIT 10;"
```

## Approving items via Slack (future)

The current MVP uses HTTP endpoints for approval. A Slack `#kb-review` integration is on the backlog (button-based approve/reject with audit trail).

## Disabling KB (e.g., for testing)

Set `KB_ENABLED=false`: the supervisor does not connect to the KB database, no distillation runs
(no LLM cost), RAG injection returns an empty string without an embedding call, and `/kb/*`
answers 503. Investigations continue without learning.

Without the flag, an unreachable Postgres has a similar effect (fail-open: reads return empty,
writes are no-ops), but the product still tries to connect at start.
