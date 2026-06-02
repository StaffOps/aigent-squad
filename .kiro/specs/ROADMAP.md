# Roadmap — AIgent-squad

**Branch de trabalho**: `dev`
**Base**: auditoria em `AUDIT.md` (2026-05-30)

Este roadmap reflete o **estado real** do projeto, não o aspiracional do README antigo. A premissa: o sistema **não builda/roda hoje** (Dockerfile raiz ausente, módulos quebrados). Antes de qualquer feature nova, estabilizar.

---

## Estado atual (honesto)

| Dimensão | Status |
|----------|--------|
| Build (`docker compose build`) | ❌ falha (Dockerfile raiz ausente) |
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
| 1 | `01-fix-blockers` | 🔴 | — | `docker compose build && up` verde |
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

> A spec 08 (CI/CD) substitui a referência a "GitLab CI" — o repo está no **GitHub**, não há pipeline configurado. A spec 07 cobre o pré-requisito de `/healthz`+`/ready` que a 05-helm-chart assume.

### Direção de produto (RCA-first)

O ganho esperado é **troubleshooting/RCA**. Caminho crítico do diferencial:

```
06 (async) → 17 (fan-out) → 18 (RCA)      ← núcleo do produto
19 (config) cedo, em paralelo              ← destrava produtização (config file + env)
21 (learning) após 18                      ← aprendizado simples (sem Knowledge Base cara)
20 (gRPC) por último                       ← maior custo; só após contratos estabilizarem
```

Princípios: comunicação eficiente (async + fan-out, gRPC quando maduro), aprendizado leve (memória de incidentes, não RAG caro), config via arquivo+env, **não complexo demais** (1 rodada de investigação na Fase 1; iteração só com promotion trigger).

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
