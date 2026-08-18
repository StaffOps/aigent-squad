---
spec: 21-incident-memory-learning
status: done-with-deferrals
completed: null
superseded_by: null
depends_on: []
deferred: ["Opus enricher (Sonnet-only for now)", "Slack approval flow"]
---

# Feature: Incident Memory & Learning

**Spec**: `21-incident-memory-learning`
**Severity**: 🟢 Feature (RCA quality improves with usage)
**Origin**: ROADMAP — "21 (learning) after 18 — learning (Sonnet extractor → Opus enricher → KB)"
**Depends on**: `18-rca-investigation-workflow` (generates the material), `22-agent-capability-manifest` (config)

Transforms ephemeral RCAs into **persistent knowledge** that accelerates future investigations. Two layers:

1. **Long-term memory (KB)** — RCAs distilled into reusable patterns
2. **RAG injection** — searches "have I seen this before?" before each new investigation

---

## User Stories

WHEN an RCA investigation completes with confidence ≥ high THEN the system SHALL distill the result into a structured pattern (`KbDelta`) and persist it in the KB.

WHEN a new investigation starts THEN the system SHALL search (via embedding similarity) the top-K similar cases in the KB and inject them into the RCA synthesizer prompt.

WHEN the KB has >0 similar cases (similarity > threshold) THEN the synthesizer SHALL use that information as a **prior** (without guaranteeing it is the answer — it may contradict).

WHEN the distillation extracts something that looks like an **architectural decision** or is below the confidence threshold THEN the item SHALL be marked for human review (not auto-approved).

WHEN the system persists KB items THEN it SHALL **redact PII and secrets** (emails, tokens, etc.) before sending to the LLM and to the store.

WHEN the KB grows THEN the system SHALL detect duplicates/supersedes (action: `supersede` instead of `create` when a similar pattern already exists).

WHEN the monthly token budget (extractor+enricher+embedding) exceeds the cap THEN new distillations SHALL be **postponed** (does not block investigation, only skips learning).

---

## Acceptance Criteria

### Storage & Schema
- [ ] PostgreSQL + pgvector container in docker-compose (1.5GB image, ~50MB RAM in use)
- [ ] Schema `kb_items` with: id, type (troubleshooting/decision/pattern/infrastructure), title, content, tags[], service_name, embedding (vector(1536)), metadata jsonb, created_at, updated_at, status (active/superseded), confidence_score
- [ ] Schema `kb_provenance`: kb_item_id, source_investigation_id, confidence, extracted_at
- [ ] HNSW index on `embedding` column for fast search
- [ ] Full-text index on `title` + `content` (fallback if embedding fails)

### Extractor & Enricher Pipeline
- [ ] `Extractor` (Sonnet) receives RCAResult → returns `list[KbDelta]`
- [ ] `Enricher` (Opus) receives drafts + original RCA → refines, generalizes, identifies patterns
- [ ] Each `KbDelta` has: action (create/supersede/noop), type, title, content, tags, service_name, confidence
- [ ] Pipeline executed AFTER investigation completes (does not block response to the user)

### Validator & Approval
- [ ] Thresholds by type: troubleshooting=0.85, decision=manual_only, pattern=0.90, infrastructure=0.80
- [ ] auto-approve if confidence ≥ threshold
- [ ] manual approval for `decision` type always + when auto-approve fails
- [ ] Endpoint `POST /kb/{id}/approve` and `POST /kb/{id}/reject`
- [ ] Pending items go to status `pending_review`

### RAG Injection
- [ ] Before the evidence fan-out, search top-K (default 3) similar cases
- [ ] Minimum similarity threshold (default 0.75) — below this, ignore
- [ ] Result injected as `<similar_cases>` in the RCA synthesizer system prompt
- [ ] Boost by `service_name` match (symptom mentions "service-x" → cases from the same service weigh more)

### Cost Control
- [ ] Configurable monthly budget (default $50/month for the learning pipeline)
- [ ] Metric `aigent.kb.distillation.cost` (counter, USD)
- [ ] Skip distillation when budget exhausted (log warning, does not fail)

### PII Redaction
- [ ] Simple patterns (regex): emails, AWS access keys, tokens (Bearer/PAT), IPs (optional)
- [ ] Applied BEFORE the LLM (extractor) and BEFORE persisting
- [ ] Replaces with `<redacted:type>` placeholder

### Tests
- [ ] Tests by separate agent, ≥80% coverage
- [ ] Test cases: extraction from simple RCA, supersede detection, RAG injection with hit/miss, threshold boundaries, PII redaction, budget cap

### Documentation
- [ ] `docs/KNOWLEDGE-BASE.md`: how it works, how to approve items, how to debug
- [ ] Update `docs/METRICS.md` with KB metrics

---

## Out of scope

- UI/dashboard for the KB (query via API for now)
- Versioning of KB items (only active/superseded)
- Cross-tenant KB sharing
- Active learning (system soliciting user feedback on applied RCA)
