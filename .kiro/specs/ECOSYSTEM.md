# Ecossistema StaffOps — Integração e Reuso

**Data**: 2026-06-02
**Escopo**: análise de `staffops-chaitops`, `staffops-anomaly-detection` e `01-DEVOPS/LABS/anomaly-detection` para reuso/integração no AIgent-squad.

> **Achado central**: existe um ecossistema StaffOps onde **boa parte do que planejamos para o AIgent-squad (specs 14, 17, 18, 19, 21) já está projetada — e em vários casos mais madura — no `staffops-chaitops`.** O fluxo que você quer (RCA + aprendizado) **já é o produto** que o chaitops + anomaly-detection formam juntos. Isto exige uma decisão de posicionamento antes de continuar implementando o AIgent-squad isolado.

---

## Os três repos

### 1. `staffops-chaitops` — plataforma de agentes (v0.2.0, MADURA)
FastAPI **agent-api** (gateway, sem CLIs) + **sidecar runners** (claude/kiro/opencode CLIs isolados). 186 testes, 83% cobertura, GitHub Actions CI, MkDocs, OTel Collector já no compose. Princípios: **no LangGraph, adapter-based, MCP-first, OTel day one, CLI-agnóstico** (os mesmos que adotamos no AIgent-squad).

Specs já escritas (`.kiro/specs/`):

| Spec chaitops | O que é | Sobrepõe à nossa spec |
|---------------|---------|----------------------|
| **multi-agent-coordinator** | `CoordinatorAdapter`: @mention fast-path OU LLM routing → fan-out `asyncio.gather` → stream aggregation com provenance → `BudgetGuard` (hard caps), cycle detection, sub-sessions no Redis, `specialists.yaml` com personas | **= nossa 17** (já totalmente projetada, com budget/ciclo/cancelamento) |
| **alert-triggered-squad** | Headless: webhook Alertmanager → squad por severity → rounds paralelos com **convergência** → síntese → Slack. Dedup, rate-limit, budget diário, SLO próprio | **= nossas 17+18 combinadas** (RCA headless, melhor que a nossa) |
| **conversation-distillation** | Conversas efêmeras → redação PII → LLM distill → schema `KbDelta` → thresholds de confiança por tipo → aprovação Slack `#kb-review` → Postgres+pgvector + injeção RAG | **= nossa 21** (aprendizado), muito mais completa |
| **agent-extensibility** | Manifests YAML auto-descobertos, `required_env` filtering, `/ready` checks | **= nossa 19** (config-driven) |
| **api-authentication** | API-key behind flag → roadmap JWT/Istio mTLS, rate-limit | **= nossa 14** (security-hardening) |
| **sidecar-architecture**, **worker-pool**, **mcp-integration**, **observability**, **storage-abstraction**, **openai-compat-bridge**, **knowledge-platform-rag**, **deferred-tasks**, **platform-agent-directives**, **ci-cd-pipeline** | infra de plataforma | cobrem nossas 06/07/08/09/10/12 em grande parte |

Tem ainda `.kiro/skills/`: `kiro-cli-auth`, `mcp-deployment`, `documentation-audit`.

### 2. `staffops-anomaly-detection` — fonte do gatilho de RCA (Go + Python ML)
Controller + workers gRPC (detecção static/adaptive Z-score/log/events), **correlation engine** (extração de workload, dedup cooldown, ≥3 sibling pods → alerta workload-level, escalação de severidade métrica+log+ML), enrichment, replay-mode, leader election, vmrules. ML: Prophet + Isolation Forest.

Spec **agent-api-integration**: quando `severity>=warning AND (ml_score>=0.7 OR corr_group>=3)`, dispara payload **enriquecido** (enrichment bundle + ML score + correlação + links Grafana/Tempo/Loki) para a Agent API do chaitops — fire-and-forget, circuit breaker, dedup, cap 64KB. **É o gatilho de RCA que entrega ~80% do diagnóstico pronto.**

### 3. `01-DEVOPS/LABS/anomaly-detection` — versão lab/antiga do #2 (ignorar; usar a #2).

---

## A constatação que importa

O **fluxo de produto RCA + aprendizado que você descreveu já existe no design do ecossistema**:

```
anomaly-detection (detecta + correla + enriquece + ML)
        │  webhook enriquecido (≥80% do diagnóstico)
        ▼
chaitops alert-triggered-squad (headless, paralelo, rounds + convergência)
        │  diagnóstico → Slack
        ▼
chaitops conversation-distillation (destila a investigação → KB → RAG)
        │
        ▼
   próximas investigações já começam com o conhecimento acumulado
```

Nossas specs 17 (multi-agente), 18 (RCA), 19 (config), 21 (learning) e 14 (security) **reimplementam peças disso**. Continuar o AIgent-squad como produto isolado = **duplicar trabalho mais maduro** (o chaitops tem testes, CI, OTel, MkDocs; o AIgent-squad nem builda hoje — ver `AUDIT.md`).

---

## O que o AIgent-squad tem de ÚNICO (e o chaitops NÃO tem)

O chaitops roda **CLIs genéricos** (claude/kiro/opencode) em sidecars. Ele **não tem domain expertise nem acesso direto a datasources**. O AIgent-squad tem exatamente isso:

| Ativo único do AIgent-squad | Por que o chaitops precisa |
|-----------------------------|----------------------------|
| **5 especialistas read-only** (aws, k8s, finops, devops, observability) com personas de domínio | O `specialists.yaml` do chaitops tem só *persona prompts* — não tem agentes que **consultam** EC2/Prometheus/Athena/GitLab de verdade |
| **Integrações de datasource** (boto3 EC2/CE/Athena, kubernetes-client, Prometheus, GitLab, docs portal) | A coleta de evidência da RCA precisa disso; o chaitops não as tem |
| **Política read-only de 4 camadas** (consultivo, nunca muta) | Crítico para um produto de RCA que investiga prod sem risco |
| **MCP server** já exposto | Ponto de integração |

Ou seja: **o AIgent-squad é a camada de "agentes internos de domínio" que o próprio README do chaitops lista como `Future > Internal Agents`** — peça que ele projetou mas não construiu.

---

## DECISÃO TOMADA (2026-06-02)

O usuário decidiu: **manter o AIgent-squad como produto SEPARADO** (não merge, não virar coletor do chaitops). Integração com o ecossistema é via **HTTP** quando necessário; **gRPC fora**; portal de docs em **inglês**.

> Nota de imparcialidade: isto é a Opção C (a que eu havia classificado como "pior" por duplicar plataforma). A decisão é legítima e de produto — mas vem com um **custo assumido**: o AIgent-squad terá que manter sua própria auth, config, OTel, CI e portal, em paralelo ao que o chaitops já tem. Para mitigar, **reusar os padrões/código do chaitops por cópia** (não por dependência) onde possível — ver "Reuso concreto" abaixo.

### O diferencial real (validado na discussão de eficiência)

O diferencial do AIgent-squad **não é comunicação entre agentes** (HTTP vs gRPC = ~3ms sobre chamadas de modelo de ~5s = ruído). O diferencial é: **especialistas leves com acesso direto às fontes de dados** (boto3, Prometheus, Athena, GitLab), que tornam o **fan-out paralelo** de uma RCA rápido e barato — sem o boot de 2–15s de um CLI-sidecar por agente. Velocidade de RCA vem de: **paralelismo (17) > evidência pré-pronta (18) > agente leve > modelo rápido no roteamento (11)**. Comunicação não entra na lista.

### Impacto nas specs (decisão = manter separado)

| Spec AIgent-squad | Veredito (manter separado) |
|-------------------|----------------------------|
| 01–05 (AUDIT) | **Manter** — fixes do código atual |
| 06 resilience / 07 probes | **Manter** — herdar padrões do chaitops por cópia (lifespan, /healthz+/ready, circuit breaker) |
| 08 CI/CD, 09 OTel, 12 terraform | **Manter** — copiar a abordagem do chaitops, não reprojetar do zero |
| 11 bedrock-cost (Haiku) | **Manter** — alto impacto em custo+latência |
| 14 security | **Manter** — copiar o padrão `api-authentication` (auth via flag) |
| 17 multi-agent (fan-out) | **Manter — É O motor de velocidade.** Reusar `BudgetGuard`+cycle detection do `multi-agent-coordinator` por cópia |
| 18 RCA | **Manter — o diferencial.** Consumir o contrato de payload do anomaly-detection (não inventar outro) |
| 19 config | **Manter** — copiar o `AgentRegistry` (já é o nosso desenho). A 22 a substitui/estende |
| **20 gRPC** | **❌ REMOVIDA** — ganho de latência irrelevante (~3ms sobre 5s). Não construir |
| 21 learning | **Manter** — reusar o schema `KbDelta` + thresholds do `conversation-distillation` por cópia |
| 22 capability-manifest | **Manter** — roster aberto + colaboração por metadados |
| 23 test-harness / 24 docs | **Manter** — docs em **inglês** |

### Reuso concreto disponível AGORA (por CÓPIA, dado "manter separado")

1. **`AgentRegistry`** (agent-extensibility) — manifests YAML + `required_env` + `/ready`. Base da nossa 19/22.
2. **`BudgetGuard` + cycle detection** (multi-agent-coordinator) — controle de custo/ciclo da nossa 17.
3. **Convergência em rounds** (alert-triggered-squad) — modelo pra dúvida "1 rodada vs iterativo" da 18.
4. **Schema `KbDelta` + thresholds + training mode** (conversation-distillation) — desenho de aprendizado da 21.
5. **Contrato de payload do anomaly-detection** (agent-api-integration) — a 18 deve **consumir** esse contrato.
6. **`kiro-cli-auth` skill** — auth API key vs SSO vs IRSA.
7. **Padrões de plataforma**: worker-pool, OpenAI-compat bridge, OTel Collector no compose, GitHub Actions CI com coverage.


