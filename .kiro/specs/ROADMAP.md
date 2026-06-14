# Roadmap — AIgent-squad

**Branch de trabalho**: `dev`
**Base**: auditoria em `AUDIT.md` (2026-05-30)

Este roadmap reflete o **estado real** do projeto, não o aspiracional do README antigo. A premissa: o sistema **não builda/roda hoje** (Dockerfile raiz ausente, módulos quebrados). Antes de qualquer feature nova, estabilizar.

---

## Estado atual (honesto)

| Dimensão | Status |
|----------|--------|
| Build (`docker compose build`) | ✅ passa (spec 01 concluída 2026-06-14) |
| Agentes unificados | ❌ 4 de 5 com código duplicado/divergente |
| Multiturno (history) | ⚠️ só aws/k8s usam de fato |
| Cache | ❌ key não-determinística + vaza entre usuários |
| Observabilidade | ⚠️ OTel presente mas mal cabeado; logs perdem contexto |
| Segurança | ❌ endpoints abertos, containers root, redis sem auth |
| Testes | ❌ inexistentes |
| Docs | ⚠️ desatualizadas (LangGraph), encoding corrompido, modelo divergente |

**Versão real sugerida**: `0.x` (pré-release). "v2.0 / Production Ready" do README é inflado (ver `version-management.md`).

---

## Fases (ordem de execução)

### Fase 0 — Estabilização (specs 01 → 02 → 03 → 04)
Pré-requisito para tudo. Ordem importa porque há dependências.

| # | Spec | Severidade | Depende de | Resultado mensurável |
|---|------|-----------|------------|----------------------|
| 1 | `01-fix-blockers` | ✅ done | — | `docker compose build && up` verde |
| 2 | `02-unify-agent-architecture` | 🟠 | 01 | 5 agentes no mesmo padrão, contrato único, multiturno |
| 3 | `03-fix-cache-observability` | 🟠/🟡 | 02 | cache determinístico, logs com contexto, OTLP |
| 4 | `04-harden-security` | 🟠 | 02 | endpoints autenticados, containers non-root |

**Critério de saída da Fase 0**: `docker compose up` sobe tudo saudável, 1 query por agente responde com contrato correto, testes passam, endpoints exigem token. Só então faz sentido falar em "deploy".

### Fase 1 — Qualidade & Docs (paralelizável após Fase 0)
- Alinhar modelo Bedrock (fonte única) — D1.
- Corrigir encoding dos docs e `ARCHITECTURE.md` (remover LangGraph) — D3, D4.
- Atualizar README com status real — D2.
- `.dockerignore`, separar `requirements.txt` por agente, mover hardcodes de `gitlab_client` para config — H1–H5.
- Suíte de testes mínima com CI (GitLab CI já tem doc inicial).

### Fase 2 — Deploy real (só após Fase 0+1 validadas)
- Terraform da infra (DynamoDB, ElastiCache, IAM/IRSA, ECR).
- **Helm chart da aplicação** — spec [`05-helm-chart`](05-helm-chart/): 7 serviços parametrizados, IRSA, External Secrets, KEDA, Argo Rollouts, NetworkPolicy, securityContext, labels obrigatórios.
- Manifests/values por ambiente (DEV/HML/PRD/BTC) com probes, `resources.requests`, labels obrigatórios (`k8s-best-practices`).
- Pipeline CI/CD (build multi-arch, scan, push Harbor/ECR).
- Validar read-only via IAM deny + RBAC reais.

> Pré-requisito de código da spec 05: adicionar endpoints `/healthz` e `/ready` (hoje só há `/health`).

### Fase 3 — Features (roadmap original, revalidado)
Só após o sistema rodar de verdade. Reaproveita o roadmap do README, mas sem inflar versão antes de uso real:
- RAG/Knowledge Bases (custo alto — avaliar ROI; ver estimativas no README).
- Slack integration (depende de `api/server.py` reescrito na spec 01).
- Agentes proativos (CronJobs).
- Demais fases 6–13 do `IMPLEMENTATION_HISTORY.md` conforme demanda.

---

## Análise cross-domain (2026-06-02) — novas specs propostas

A análise em [`ANALYSIS.md`](ANALYSIS.md) (8 specialists em paralelo) encontrou problemas estruturais **além** do AUDIT. Resumo: mesmo depois de buildar, o sistema **não tem concorrência** (I/O síncrono em handlers async), **falha fechada** (Redis/DynamoDB sem tratamento de erro), e é **cego/indefensável** (telemetria quebrada, read-only só no prompt). Novas specs propostas:

| # | Spec | Severidade | Depende de |
|---|------|-----------|------------|
| 06 | `resilience-patterns` (async-first, fail-open, circuit breaker, classifier fallback) | 🔴 | 02 |
| 07 | `readiness-probes` (`/healthz`+`/ready`+graceful shutdown) | 🔴 | 02 |
| 08 | `ci-cd-pipeline` (GitHub Actions, multi-arch, scan, coverage gate) | 🔴 | 01 |
| 09 | `otel-instrumentation` (7 serviços, propagação, Collector) | 🟠 | 02 |
| 10 | `metrics-and-cost-observability` (RED + tokens/custo $) | 🟠 | 09 |
| 11 | `bedrock-resilience-cost` (Haiku no classifier, prompt caching, tiering) | 🟠 | 06 |
| 12 | `terraform-infra` (DynamoDB/ElastiCache/ECR/IRSA/Secrets) | 🟠 | — |
| 13 | `iam-least-privilege` (read-only por agente + deny explícito) | 🟠 | 12 |
| 14 | `security-hardening` (threat model, authn/audit, NetworkPolicy/mTLS, prompt guardrails) | 🟠 | 04 |
| 15 | `sli-slo-framework` | 🟡 | 10 |
| 16 | `incident-runbooks` | 🟡 | 06, 07 |
| 17 | `multi-agent-collaboration` (fan-out/fan-in cross-domain + síntese, agent-as-tools 1 salto) | 🟢 | 06, 09 |
| 18 | `rca-investigation-workflow` (**diferencial**: evidência paralela → timeline → correlação → RCA, read-only) | 🟢 | 17, 09, 19 |
| 19 | `config-driven-platform` (YAML + env override, secrets fora do YAML, registry de agentes) | 🟠 | — |
| ~~20~~ | ~~`grpc-inter-agent-mesh`~~ — **REMOVIDA** (2026-06-02): latência irrelevante vs chamadas de modelo | ❌ | — |
| 21 | `incident-memory-learning` (memória de incidentes + recuperação de similares; aprendizado simples) | 🟢 | 18 |
| 22 | `agent-capability-manifest` (roster **aberto** via YAML + colaboração por metadados `capabilities`/`evidence_types`/`delegates_to`; **substitui a 19**) | 🟠 | — |
| 23 | `test-harness-docker` (`Dockerfile.test` + `pytest --cov-fail-under=90` com mocks; mesmo harness dev↔CI; consumido pela 08) | 🔴 | — |
| 24 | `docs-portal-mkdocs` (portal MkDocs Material `src`→`public`; README vira índice; ADRs; consolida/deleta docs fantasma) | 🟠 | — |

> Ver [`ECOSYSTEM.md`](ECOSYSTEM.md): **decisão (2026-06-02) = manter SEPARADO**. Specs 14/17/19/21 existem mais maduras no `staffops-chaitops` → reusar **por cópia** (não dependência). gRPC (20) removida. Docs em inglês. Diferencial real = especialistas leves com acesso direto a dados (não comunicação).

> Ver [`EVIDENCE-MODEL.md`](EVIDENCE-MODEL.md): catálogo de sinais cross-domain + regra de correlação por camadas causais + teste de independência (deliberação observability+sre+troubleshoot, 2026-06-02). Substitui o "≥3 sinais" ingênuo. Alimenta a spec 18 (modelo de evidência) e as specs 09/10 (catálogo de métricas).

> A spec 08 (CI/CD) substitui a referência a "GitLab CI" — o repo está no **GitHub**, não há pipeline configurado. A spec 07 cobre o pré-requisito de `/healthz`+`/ready` que a 05-helm-chart assume.

### Direção de produto (RCA-first)

O ganho esperado é **troubleshooting/RCA**. Caminho crítico do diferencial:

```
06 (async) → 17 (fan-out) → 18 (RCA)      ← núcleo do produto
19 (config) cedo, em paralelo              ← destrava produtização (config file + env)
21 (learning) após 18                      ← aprendizado (Sonnet extractor → Opus enricher → KB)
```

Princípios: comunicação eficiente (async + fan-out), aprendizado com qualidade (Opus enriquece antes de persistir), config via arquivo+env, **não complexo demais** (limites de rodada como hard stop).

### Limites de rodadas por nível

| Nível | max_rounds | Modelo synthesizer | Custo/RCA (5 agentes) |
|-------|------------|-------------------|------------------------|
| 1 (MVP) | 1 | Sonnet | ~$0.17 |
| 2 (iterativo) | 5 | Sonnet | ~$0.80 |
| 3 (contexto compartilhado) | 10 | Opus | ~$1.65 |
| 4 (autônomo) | 25 | Opus | ~$4.20 |

### Checklist de milestone (obrigatório a cada entrega)

Per `documentation-sync.md` — ao fechar qualquer fase/milestone:

- [ ] Specs tocadas refletem o que foi **implementado** (não o planejado — corrigir divergências)
- [ ] ROADMAP atualizado (status, datas, items concluídos)
- [ ] README atualizado se mudou algo visível ao usuário
- [ ] CHANGELOG entry (se bump de versão)
- [ ] Testes passando (≥90% cobertura)
- [ ] Custos reais medidos vs estimativas (ajustar tabela no ANALYSIS.md se divergir >30%)

---

## Princípios de versionamento (deste ponto em diante)

Per `version-management.md`:
- Bump só com **resultado mensurável** em ambiente alvo, não por feature implementada.
- Não voltar a "Production Ready" antes de deploy estável + testes + validação por operador.
- CHANGELOG consolidado a cada milestone, não a cada commit.

---

## Mapa specs ↔ achados

| Spec | Achados (AUDIT.md) |
|------|--------------------|
| 01-fix-blockers | B1, B2, B3, B4 |
| 02-unify-agent-architecture | A1, A2, A3, H1 |
| 03-fix-cache-observability | C1, C2, O1, O2, O3, O4, D5 |
| 04-harden-security | S1, S2, S3, S4, S5 |
| Fase 1 (docs/higiene) | D1, D2, D3, D4, H2, H3, H4, H5, testes |

---

## Long-term Vision: Autonomous Multi-Agent System

**Norte**: sistema de agentes autônomos com memória compartilhada e raciocínio multi-step.
**Abordagem**: evolução incremental — cada nível só se justifica quando o anterior prova limitação mensurável.

### Nível 1 — Hub-and-spoke com fan-out (specs atuais)

O supervisor orquestra; agentes coletam evidência independentemente; synthesizer correlaciona.

- **Entrega**: RCA em ~5s com evidência cruzada de N agentes em paralelo.
- **Limitação esperada**: coleta cega — cada agente não sabe o que os outros encontraram.
- **Promotion trigger para Nível 2**: RCA de 1 rodada é insuficiente em >30% dos casos (evidência incompleta, gaps não cobertos).

### Nível 2 — Investigação iterativa

Synthesizer detecta gaps na evidência → dispara 2ª rodada direcionada (agentes específicos, perguntas refinadas).

- **Entrega**: investigação adaptativa que aprofunda onde a 1ª rodada foi fraca.
- **Limitação esperada**: agentes ainda operam isolados — refinam sem saber o que outros acharam.
- **Promotion trigger para Nível 3**: contexto de outros agentes melhoraria a coleta em >20% dos casos (ex: observability sabendo que devops encontrou deploy recente mudaria a query de métricas).

### Nível 3 — Memória compartilhada + contexto cruzado

Agentes recebem resumo do que os outros coletaram (blackboard/scratchpad compartilhado). Cada agente pode refinar sua coleta com base nas descobertas alheias. Não é conversa P2P — é 1 broadcast de contexto → coleta informada.

- **Entrega**: evidência que se reforça (agente A encontra deploy → agente B foca métricas pós-deploy → correlação mais precisa).
- **Limitação esperada**: fluxo ainda orquestrado pelo supervisor; agentes não tomam decisão de "preciso investigar X que ninguém pediu".
- **Promotion trigger para Nível 4**: o sistema precisa de autonomia real — decisão sem humano no loop, auto-trigger, hipóteses emergentes que nenhum agente individual proporia.
- **Custo**: cada rodada com contexto = mais tokens (N resumos × M agentes). Validar ROI antes de avançar.

### Nível 4 — Agentes autônomos com raciocínio multi-step

Agentes propõem hipóteses, delegam entre si, iteram até convergir em RCA. Memória de longo prazo compartilhada. Auto-trigger (detecta sintoma → investiga sem esperar humano). Convergência por votação/confiança, não por rodada fixa.

- **Entrega**: sistema que resolve problemas emergentes que nenhum nível anterior resolveria.
- **Riscos**: custo de tokens explosivo, loops infinitos, decisões incorretas sem supervisão.
- **Guardrails obrigatórios**: budget cap por investigação, max iterations, human-in-the-loop para ações (read-only para coleta), kill switch.
- **Pré-requisitos**: Níveis 1–3 validados + métricas de qualidade de RCA + custo controlado.

### Princípios da evolução

- **Cada nível prova valor antes de avançar** — não construir Nível 3 sem evidência de que Nível 2 é insuficiente.
- **Promotion triggers são mensuráveis** — não "parece que precisamos", mas "em X% dos casos, Y falhou por Z".
- **Custo é constraint real** — cada nível multiplica tokens. Medir $/investigação em cada nível.
- **Read-only é invariante** — em todos os níveis, agentes coletam e analisam. Nunca executam fix automaticamente (sem humano aprovando).
