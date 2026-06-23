# Design: Incident Memory & Learning

**Spec**: `21-incident-memory-learning`
**Depende de**: 18 (RCA workflow), 19 (config)
**Reusa de**: `staffops-chaitops` conversation-distillation (por cópia, não dependência)

---

## Visão Geral

Transforma investigações RCA efêmeras em conhecimento persistente e recuperável. Duas camadas: **memória longa** (KB destilada) e **RAG injection** (buscar "já vi isso antes?" no início de cada nova investigação).

---

## Arquitetura

```
Investigação RCA (spec 18) completa
         ↓
Sonnet EXTRACTOR (draft): extrai KbDelta[] do resultado
         ↓
Opus ENRICHER (refine): generaliza, encontra padrões, melhora redação
         ↓
Validator (thresholds por tipo)
         ↓
   ├── auto_approve → persist (Postgres+pgvector + S3)
   ├── send_to_review → Slack #kb-review
   └── discard → log only

Nova investigação inicia:
         ↓
RAG query (embedding do sintoma) → top-k KB items → injetado no prompt do orchestrator
```

---

## Componentes

| Componente | Responsabilidade |
|-----------|------------------|
| **Extractor** (Sonnet) | Extrair fatos estruturados da RCA: sintoma, causa, evidência, fix, prevenção → `list[KbDelta]` |
| **Enricher** (Opus) | Recebe draft do extractor + RCA completa → generaliza, identifica padrões cross-incident, melhora redação pra reuso futuro |
| **Validator** | Thresholds de confiança por tipo (troubleshooting/decision/pattern/infrastructure) |
| **KbStore** (Postgres + pgvector) | Metadata + embedding + full-text search |
| **RAG injector** | query_top_k por embedding similarity + boost por service_name → formata system prompt |
| **Approval flow** | Slack #kb-review com botões (approve/reject/edit) |

---

## Model Pipeline (Sonnet → Opus)

**Por que dois modelos em série em vez de Opus direto?**

| Abordagem | Custo | Qualidade |
|-----------|-------|-----------|
| Só Sonnet | ~$0.03 | Bom em extração factual; fraco em generalização |
| Só Opus | ~$0.50 | Excelente mas caro se precisar re-gerar |
| **Sonnet draft → Opus refine** | ~$0.27 | Opus recebe draft estruturado → trabalho menor → output melhor por $ gasto |

O Sonnet faz o trabalho pesado (estruturar), o Opus faz o trabalho inteligente (enriquecer). Separation of concerns por custo-qualidade.

**Promotion trigger (demotion)**: se Opus não adiciona valor mensurável em >60% das destilações (output ≈ input do Sonnet), rebaixar para Sonnet-only.

---

## Reuso do chaitops (por cópia)

| Conceito do chaitops | Aplicação aqui |
|---------------------|----------------|
| `KbDelta` model (action: create/supersede/noop) | Mesmo schema |
| Confidence thresholds por tipo | Mesma lógica (troubleshooting=0.85, decision=always human, pattern=0.90, infrastructure=0.80) |
| pgvector embedding + HNSW index | Mesmo approach (text-embedding-3-small, 1536 dims) |
| CostGuard (budget cap + rate limit) | Mesmo pattern |
| Slack approval flow | Mesmo UX |
| Alert-as-conversation | Webhook do spec 18 → investigação → destilação |

**NÃO reusar**: hot buffer Redis (desnecessário — nossas "conversas" são investigações curtas, não chats longos), nem a arquitetura three-tier (overkill para o volume do AIgent-squad).

---

## Separação memória curta vs longa

| Tipo | Spec | O que é | Storage | Lifetime |
|------|------|---------|---------|----------|
| **Curta** (scratchpad) | 18 | Evidência DURANTE uma investigação | Dict/Redis hash | Duração da investigação |
| **Longa** (KB) | 21 (esta) | RCA destilada: sintoma→causa→fix→prevenção | Postgres + pgvector | Permanente |

A memória curta é consumida durante a investigação (Níveis 2–4). A memória longa é consultada NO INÍCIO (RAG injection) para acelerar diagnósticos futuros.

---

## Invariantes

- KB items NUNCA contêm secrets/PII (redação antes do LLM).
- `decision` type NUNCA auto-approve (sempre revisão humana).
- Enricher Opus é chamado 1× por destilação (não por KB item).
- Budget cap é hard stop ($50/mês default).
- RAG injection é best-effort: falha → investigação continua sem, não bloqueia.

---

## Custos

| Componente | Por destilação | Mensal (50 incidentes) |
|-----------|---------------|------------------------|
| Extractor (Sonnet) | ~$0.03 | ~$1.50 |
| Enricher (Opus) | ~$0.24 | ~$12.00 |
| Embedding (text-embedding-3-small) | ~$0.001 | ~$0.05 |
| Postgres (container Phase 1) | $0 | $0 |
| **Total** | **~$0.27** | **~$13.55** |

Comparativo: uma re-investigação manual evitada (~$50–100/h × 30min) = $25–50. KB que evita 1 re-investigação/mês já paga 4× o custo.
