# Design: Incident Memory & Learning

**Spec**: `21-incident-memory-learning`
**Depends on**: 18 (RCA workflow), 19 (config)
**Reuses from**: `staffops-chaitops` conversation-distillation (by copy, not dependency)

---

## Overview

Transforms ephemeral RCA investigations into persistent and retrievable knowledge. Two layers: **long-term memory** (distilled KB) and **RAG injection** (search "have I seen this before?" at the start of each new investigation).

---

## Architecture

```
RCA Investigation (spec 18) completes
         ↓
Sonnet EXTRACTOR (draft): extracts KbDelta[] from the result
         ↓
Opus ENRICHER (refine): generalizes, finds patterns, improves writing
         ↓
Validator (thresholds by type)
         ↓
   ├── auto_approve → persist (Postgres+pgvector + S3)
   ├── send_to_review → Slack #kb-review
   └── discard → log only

New investigation starts:
         ↓
RAG query (symptom embedding) → top-k KB items → injected into orchestrator prompt
```

---

## Components

| Component | Responsibility |
|-----------|----------------|
| **Extractor** (Sonnet) | Extract structured facts from the RCA: symptom, cause, evidence, fix, prevention → `list[KbDelta]` |
| **Enricher** (Opus) | Receives draft from extractor + full RCA → generalizes, identifies cross-incident patterns, improves writing for future reuse |
| **Validator** | Confidence thresholds by type (troubleshooting/decision/pattern/infrastructure) |
| **KbStore** (Postgres + pgvector) | Metadata + embedding + full-text search |
| **RAG injector** | query_top_k by embedding similarity + boost by service_name → formats system prompt |
| **Approval flow** | Slack #kb-review with buttons (approve/reject/edit) |

---

## Model Pipeline (Sonnet → Opus)

**Why two models in series instead of Opus directly?**

| Approach | Cost | Quality |
|----------|------|---------|
| Sonnet only | ~$0.03 | Good at factual extraction; weak at generalization |
| Opus only | ~$0.50 | Excellent but expensive if regeneration is needed |
| **Sonnet draft → Opus refine** | ~$0.27 | Opus receives a structured draft → less work → better output per $ spent |

Sonnet does the heavy lifting (structuring), Opus does the intelligent work (enriching). Separation of concerns by cost-quality.

**Promotion trigger (demotion)**: if Opus does not add measurable value in >60% of distillations (output ≈ Sonnet's input), demote to Sonnet-only.

---

## Reuse from chaitops (by copy)

| Concept from chaitops | Application here |
|----------------------|------------------|
| `KbDelta` model (action: create/supersede/noop) | Same schema |
| Confidence thresholds by type | Same logic (troubleshooting=0.85, decision=always human, pattern=0.90, infrastructure=0.80) |
| pgvector embedding + HNSW index | Same approach (text-embedding-3-small, 1536 dims) |
| CostGuard (budget cap + rate limit) | Same pattern |
| Slack approval flow | Same UX |
| Alert-as-conversation | Webhook from spec 18 → investigation → distillation |

**DO NOT reuse**: hot buffer Redis (unnecessary — our "conversations" are short investigations, not long chats), nor the three-tier architecture (overkill for AIgent-squad's volume).

---

## Short-term vs long-term memory separation

| Type | Spec | What it is | Storage | Lifetime |
|------|------|------------|---------|----------|
| **Short-term** (scratchpad) | 18 | Evidence DURING an investigation | Dict/Redis hash | Duration of the investigation |
| **Long-term** (KB) | 21 (this) | Distilled RCA: symptom→cause→fix→prevention | Postgres + pgvector | Permanent |

Short-term memory is consumed during the investigation (Levels 2–4). Long-term memory is consulted AT THE BEGINNING (RAG injection) to accelerate future diagnoses.

---

## Invariants

- KB items NEVER contain secrets/PII (redaction before the LLM).
- `decision` type NEVER auto-approves (always human review).
- Enricher Opus is called 1× per distillation (not per KB item).
- Budget cap is a hard stop ($50/month default).
- RAG injection is best-effort: failure → investigation continues without it, does not block.

---

## Costs

| Component | Per distillation | Monthly (50 incidents) |
|-----------|-----------------|------------------------|
| Extractor (Sonnet) | ~$0.03 | ~$1.50 |
| Enricher (Opus) | ~$0.24 | ~$12.00 |
| Embedding (text-embedding-3-small) | ~$0.001 | ~$0.05 |
| Postgres (container Phase 1) | $0 | $0 |
| **Total** | **~$0.27** | **~$13.55** |

Comparison: one avoided manual re-investigation (~$50–100/h × 30min) = $25–50. KB that avoids 1 re-investigation/month already pays for itself 4× over.
