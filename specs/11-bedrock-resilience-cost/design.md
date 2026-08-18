# Design: Bedrock Cost & Model Tiering

## Architecture

Thin layer over `bedrock.py` (already async from spec 06): chooses model by role, enables caching, applies budget.

```
role (classifier|agent|synthesis) ─▶ model_tier (config 19/22) ─▶ Bedrock model
large system prompt ─▶ cache_control: ephemeral ─▶ ~90% discount on repeated input
session ─▶ token budget (hard cap) ─▶ cuts before exploding
```

## Components

| Component | Responsibility | Where |
|-----------|----------------|-------|
| Model resolver | role → model (from config) | `src/core/bedrock.py` |
| Prompt cache | `cache_control: ephemeral` in system block | `src/core/bedrock.py` |
| Token budget | hard cap per session + truncation by tokens | `src/core/bedrock.py` / classifier |
| Cost emit | metric input/output per agent+model | `src/core/bedrock.py` (hook for spec 10) |

## Cost (order of magnitude, @200 queries/day — from `ANALYSIS.md`)

| Item | Current | With this spec |
|------|---------|----------------|
| Classifier (Sonnet→Haiku) | ~$24/mo | ~$2/mo |
| System prompt without caching | ~$103/mo wasted | ~$10/mo |
| **Combined effect** | — | **~$207→~$99/mo** |

## Decisions and trade-offs

### Decision 1: Model tiering per layer (Haiku / Sonnet / Opus)

**Choice**: each system layer uses the most cost-effective model for its complexity.

**Tiering table (config-driven, not hardcoded):**

| Layer | Model | Justification | Cost/call |
|-------|-------|---------------|-----------|
| Classifier (routing) | **Haiku** | Simple classification (which agent?). 1/13 the cost, less latency. | ~$0.001 |
| Collector agents (evidence) | **Sonnet** | Directed datasource query, moderate reasoning. | ~$0.02 |
| Synthesizer Level 1–2 (correlation) | **Sonnet** | Correlation with ≤5 evidence rounds. Sufficient. | ~$0.05 |
| Synthesizer Level 3–4 (complex correlation) | **Opus** | Multi-round correlation (10–25 rounds), deep causal reasoning. | ~$0.50 |
| Distillation extractor (draft KB) | **Sonnet** | Extract structured facts from investigation. High volume, OK quality. | ~$0.03 |
| Distillation enricher (refine KB) | **Opus** | Generalize, find non-obvious patterns, write for future reuse. Quality > speed. | ~$0.24 |

**Promotion triggers between models:**
- Synthesizer Sonnet→Opus: RCA with 'low' confidence in >40% of Level 3+ cases.
- Enricher Opus→Sonnet (demotion): Opus doesn't add measurable value in >60% of distillations (output ≈ Sonnet input).

**Trade-off**: Haiku may misroute on very ambiguous queries → mitigated by classifier fallback (spec 06) and multi-agent fan-out (spec 17) covering multiple domains.
**When to reopen**: if measurements show routing accuracy drop with Haiku > acceptable threshold.

### Decision 2: Re-enable prompt caching
**Choice**: enable `cache_control: ephemeral` in system block (was commented "for compatibility").
**Justification**: the system prompt (~6400 tokens) is identical across calls; caching gives ~90% discount on cached input within the window. The "compatibility" reason needs investigation — Bedrock has supported it since 2024.
**Trade-off**: validate support on the chosen model at startup; if unavailable, degrade (no cache) without breaking.

## Invariants
- Model per role comes from **config** (specs 19/22), never hardcoded.
- Session budget is a **hard cap** (cuts, doesn't merely warn).
- Caching unavailable → degrade without breaking.

## External dependencies
| Service | Usage |
|---------|-------|
| Bedrock | Haiku (classifier) + Sonnet (agent/synthesis) + prompt caching |

## Verification
```bash
docker run --rm -v "$PWD:/app" -w /app python:3.11-slim sh -c \
  "pip install -q -r requirements.txt -r requirements-dev.txt && pytest tests/ -v --cov=src --cov-fail-under=90"
```
Tests (mocked Bedrock): classifier uses Haiku / agent uses Sonnet; `cache_control` present in body; exceeded budget cuts; history truncated by tokens.

## Risks
- Haiku degrades routing → fallback (06) + fan-out (17) cover; measure accuracy.
- Caching "incompatible" (reason for the original comment) → validate at startup, degrade if needed.
