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
**Severidade**: 🟢 Feature (qualidade da RCA aumenta com uso)
**Origem**: ROADMAP — "21 (learning) após 18 — aprendizado (Sonnet extractor → Opus enricher → KB)"
**Depende de**: `18-rca-investigation-workflow` (gera o material), `22-agent-capability-manifest` (config)

Transforma RCAs efêmeras em **conhecimento persistente** que acelera investigações futuras. Duas camadas:

1. **Memória longa (KB)** — RCAs destiladas em padrões reutilizáveis
2. **RAG injection** — busca "já vi isso antes?" antes de cada nova investigação

---

## User Stories

WHEN uma investigação RCA é concluída com confiança ≥ alta THEN o sistema SHALL destilar o resultado em padrão estruturado (`KbDelta`) e persistir no KB.

WHEN uma nova investigação inicia THEN o sistema SHALL buscar (via embedding similarity) os top-K casos similares no KB e injetar no prompt do RCA synthesizer.

WHEN o KB tem >0 casos similares (similarity > threshold) THEN o synthesizer SHALL usar essa informação como **prior** (sem garantir que é a resposta — pode contradizer).

WHEN a destilação extrai algo que parece **decisão arquitetural** ou está abaixo do threshold de confiança THEN o item SHALL ser marcado para revisão humana (não auto-aprova).

WHEN o sistema persiste KB items THEN ele SHALL **redatar PII e secrets** (emails, tokens, etc) antes de enviar ao LLM e ao banco.

WHEN o KB cresce THEN o sistema SHALL detectar duplicatas/supersedes (action: `supersede` em vez de `create` quando padrão similar já existe).

WHEN o budget mensal de tokens (extractor+enricher+embedding) ultrapassa o cap THEN novas destilações SHALL ser **postergadas** (não bloqueia investigação, só skip de aprendizado).

---

## Acceptance Criteria

### Storage & Schema
- [ ] PostgreSQL + pgvector container no docker-compose (1.5GB image, ~50MB RAM em uso)
- [ ] Schema `kb_items` com: id, type (troubleshooting/decision/pattern/infrastructure), title, content, tags[], service_name, embedding (vector(1536)), metadata jsonb, created_at, updated_at, status (active/superseded), confidence_score
- [ ] Schema `kb_provenance`: kb_item_id, source_investigation_id, confidence, extracted_at
- [ ] HNSW index na coluna `embedding` para busca rápida
- [ ] Full-text index em `title` + `content` (fallback se embedding falhar)

### Extractor & Enricher Pipeline
- [ ] `Extractor` (Sonnet) recebe RCAResult → retorna `list[KbDelta]`
- [ ] `Enricher` (Opus) recebe drafts + RCA original → refina, generaliza, identifica padrões
- [ ] Cada `KbDelta` tem: action (create/supersede/noop), type, title, content, tags, service_name, confidence
- [ ] Pipeline executado APÓS investigação concluir (não bloqueia resposta ao usuário)

### Validator & Approval
- [ ] Thresholds por tipo: troubleshooting=0.85, decision=manual_only, pattern=0.90, infrastructure=0.80
- [ ] auto-approve se confidence ≥ threshold
- [ ] manual approval para `decision` type sempre + auto-approve falha
- [ ] Endpoint `POST /kb/{id}/approve` e `POST /kb/{id}/reject`
- [ ] Pendente vai pra status `pending_review`

### RAG Injection
- [ ] Antes do fan-out de evidência, busca top-K (default 3) casos similares
- [ ] Threshold de similaridade mínima (default 0.75) — abaixo disso, ignora
- [ ] Resultado injetado como `<similar_cases>` no system prompt do RCA synthesizer
- [ ] Boost por `service_name` match (sintoma menciona "service-x" → casos do mesmo serviço pesam mais)

### Cost Control
- [ ] Budget mensal configurável (default $50/mês para o pipeline de aprendizado)
- [ ] Métrica `aigent.kb.distillation.cost` (counter, USD)
- [ ] Skip distillation quando budget exhausted (log warning, não falha)

### PII Redaction
- [ ] Patterns simples (regex): emails, AWS access keys, tokens (Bearer/PAT), IPs (opcional)
- [ ] Aplicado ANTES do LLM (extractor) e ANTES de persistir
- [ ] Substitui por `<redacted:type>` placeholder

### Tests
- [ ] Tests por agent separado, ≥80% coverage
- [ ] Test cases: extraction de RCA simples, supersede detection, RAG injection com hit/miss, threshold boundaries, PII redaction, budget cap

### Documentation
- [ ] `docs/KNOWLEDGE-BASE.md`: como funciona, como aprovar items, como debugar
- [ ] Atualizar `docs/METRICS.md` com métricas KB

---

## Fora de escopo

- UI/dashboard pro KB (consulta via API por enquanto)
- Versionamento de KB items (só active/superseded)
- Cross-tenant KB sharing
- Active learning (sistema solicitar feedback do usuário sobre RCA aplicada)
