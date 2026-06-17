# Competitive Analysis — AI SRE / Incident-RCA Agents

**Última atualização**: 2026-06-16
**Método**: leitura de READMEs, configs e estrutura de 6 projetos open-source em
`example-sres/` + produtos comerciais (Datadog Bits, incident.io, PagerDuty,
Azure SRE Agent) via material público. Para a maioria, baseado em READMEs + estrutura;
**HolmesGPT teve deep dive de código** (peer de referência). Aurora/OpenSRE: deep
code read ainda pendente.

> Escopo: posicionamento e direção de produto. Para decisões de arquitetura ver
> `.kiro/specs/ADR-001` e `.kiro/specs/14-security-hardening/`.

---

## TL;DR

- O espaço "AI SRE que investiga e faz RCA" está **lotado e amadurecendo rápido**.
  Os mais próximos/maduros do nosso: **Aurora** (Arvo) e **OpenSRE** (Tracer).
- O eixo onde estamos quase sozinhos: **read-only por padrão + segurança
  defense-in-depth desde o início**. A maioria corre para autonomia (agir);
  nós começamos pela disciplina de segurança e habilitamos execução depois.
- **Read-only é a postura atual, não permanente** (ver `READ_ONLY_POLICY.md`).
  A vantagem: construímos o guardrail ANTES de agir; concorrentes que já agem
  tiveram que adicionar guardrail depois (Aurora adicionou NeMo + Sigma).

---

## Matriz comparativa

| Projeto | Categoria | Stack | Autonomia (age?) | Multi-agente | Guardrails / segurança | Maturidade |
|---------|-----------|-------|------------------|--------------|------------------------|------------|
| **AIgent-squad** (nós) | AI SRE / RCA consultivo | Python, **Bedrock direto** | **Read-only hoje** (execução = futuro com HITL) | supervisor + classifier + fan-out + synthesizer | spec 14 (Bedrock Guardrails, fail-closed, multi-idioma) — **escrita, não impl** | pré-1.0, não deployado |
| **Aurora** (Arvo-AI) | Incident investigation/RCA | Python, Flask, Celery, **LangGraph**, Next.js | **Age**: roda CLI em pods sandboxed, sugere PRs | LangGraph multi-agente, 30+ tools | **NeMo input rail (anti-injection) + 37 regras SigmaHQ + allow/denylist por org** | Apache-2, ativo (Discord, demos) |
| **HolmesGPT** (Robusta/MS, **CNCF**) | AI SRE / RCA + operator 24/7 | Python, FastAPI, Pydantic, **litellm** (multi-LLM incl. Bedrock) | **Read-only por design, respeita RBAC, "safe in prod"** (mas tem `ApprovalRequirement` p/ HITL e operator pode abrir PRs via GitHub) | agentic loop single + `max_steps`; toolsets YAML | sanitização de params (`shlex.quote`), **safeguards anti-loop**, RBAC; sem guardrail anti-injection dedicado | **CNCF sandbox**, muito ativo, ~46 toolsets builtin |
| **OpenDerisk** (derisk-ai) | DeepResearch RCA | Python (deriva do DB-GPT), monorepo `uv` | Investiga; **Code-Agent gera código** dinâmico | **5 agentes**: SRE, Code, Report, Vis, Data | não evidente no README | MIT, V0.2, dataset OpenRCA (microsoft) |
| **OpenSRE** (Tracer-Cloud) | Framework + **RL env / benchmark** p/ AI SRE | Python, `uv`, ruff/mypy | **Sugere e, opcionalmente, executa remediação** | framework p/ você montar, 60+ tools | tem SECURITY + trust center | Apache-2, **pre-alpha**, trending |
| **SmythOS / sre** | **Runtime/SDK de agentes** (não é SRE-específico) | **TypeScript**, pnpm monorepo | plataforma genérica de agentes | orquestração genérica | "security built-in", abstrações de recurso | MIT, maduro, SDK+CLI |
| **sre-agent** | AI SRE diagnóstico | Python 3.13, **Anthropic direto** | **Read-only** (diagnostica, sugere fix, posta no Slack) | single-agent + **MCP** | bandit no CI | pip-installável, simples/focado |
| **versus-incident** | Incident routing + AI detect | **Go**, Helm | Detecta anomalia em log; **roteia** (não remedia) | motor regras + AI agent | — | MIT, AI agent beta, on-call integrations |

---

## Onde estamos À FRENTE

1. **Read-only por design + caminho de execução seguro pré-construído.** Quando
   formos agir, a defesa (spec 14) já existe. Aurora/OpenSRE agem e tiveram que
   correr atrás do guardrail. Ordem importa: guardrail antes de agir > depois.
2. **Defesa anti-prompt-injection multi-idioma planejada como camada própria**
   (Bedrock Guardrails, independente do modelo). Poucos têm isso de verdade
   (Aurora é a exceção). Azure SRE Agent **só suporta inglês** — gap nosso a
   explorar.
3. **Bedrock direto, sem framework** (ADR-001) — menos lock-in e superfície que
   Aurora (LangGraph) ou os que dependem de runtime próprio.
4. **Atribuição de custo por agente** (spec 27, AIP + métrica) — maturidade
   FinOps que nenhum dos exemplos destaca.

## Onde estamos ATRÁS (lacunas honestas)

1. **Segurança ainda é spec, não código.** Aurora **já roda** NeMo + Sigma em
   produção. Nosso diferencial de segurança só é real quando a spec 14 for
   implementada. → **prioridade**.
2. **Sem benchmark/medição de qualidade de RCA.** OpenSRE tem suíte sintética
   *scored* (root-cause accuracy, evidência exigida, red herrings adversariais).
   Não sabemos medir se nossa RCA é boa. → **maior gap de produto**.
3. **Maturidade/deploy.** Todos têm releases/stars/comunidade; nós não
   deployamos ainda.
4. **Visualização da cadeia de evidência.** OpenDerisk renderiza o evidence
   chain (protocolo Vis). Temos o synthesizer, falta a visualização.
5. **Catálogo de tools/integrações.** Aurora 30+, OpenSRE 60+. Temos boto3/k8s/
   http/athena/mcp — bom começo, mas catálogo menor.

---

## Ideias dignas de "roubar" (priorizadas)

| Origem | Ideia | Por que pra nós | Encaixa em |
|--------|-------|-----------------|------------|
| **HolmesGPT** | **Context-window management**: server-side filtering + spill de resultado grande p/ disco + transformer `llm_summarize` p/ output de tool | Resolve direto a dor que vimos (MCP trouxe 165KB de eventos → input tokens). Reduz custo e evita OOM | spec nova / adapters + spec 27 |
| **HolmesGPT** | **`ApprovalRequirement`** por-tool (`needs_approval` + `reason` + `prefixes_to_save`) | Modelo pronto de human-in-the-loop p/ QUANDO formos executar — granular por tool/comando | execução futura + spec 14 |
| **HolmesGPT** | **`safeguards.prevent_overly_repeated_tool_call`** (anti-loop barato) | Conté custo/loop sem ML; complementa nosso max_rounds | spec 17/18 + spec 14 (abuso) |
| **HolmesGPT** | **Toolsets YAML** com `prerequisites`, `transformers`, `expose_remotely` (cross-cluster via MCP) | Mais rico que nossos adapters; `prerequisites` (checa `kubectl version` antes) e jq-query paginado evitam overflow | adapters / agent.yaml |
| **HolmesGPT** | **litellm** como camada multi-provider | Trocaríamos lock-in do boto3-bedrock por abstração multi-LLM (OpenAI/Anthropic/Bedrock/Gemini) sem reescrever | reavaliar vs ADR-001 |
| **OpenSRE** | Suíte sintética **scored** de RCA (accuracy, evidência, red herrings) | Resolve "como sei se a RCA é boa?"; casa com nosso gate de testes ≥90% | spec nova / 18-rca |
| **Aurora** | **NeMo Guardrails** input rail + regras **SigmaHQ** p/ comandos | Complementa/alternativa ao Bedrock Guardrails; Sigma é ouro p/ quando formos executar | spec 14 |
| **versus-incident** | Modos **training / shadow / detect** | Introduzir detecção/ação SEM risco (shadow = "would have alerted") | roadmap agentes proativos / execução futura |
| **OpenDerisk** | **Visualização do evidence chain** + multiagente por papel (Report/Vis/Data) | Torna a RCA audível/explicável ao operador | spec 18 / UI |
| **Aurora** | **Allow/denylist de comandos por org** | Pré-requisito de governança QUANDO formos executar | execução futura + spec 14 |
| **sre-agent** | Setup wizard CLI + simplicidade de onboarding | Reduz atrito de adoção | DX / docs |
| **OpenSRE / Aurora** | Dependency graph traversal na investigação | Correlação mais rica que fan-out cego | spec 17/18 |

---

## Posicionamento (como nos vendemos)

> **"O AI SRE read-only por padrão — que investiga com rigor e, quando for agir,
> agirá com guardrails que os outros só adicionaram depois de já estarem agindo."**

- **Não competimos (hoje) em autonomia** — competimos em **confiança
  verificável** e **rigor de RCA**.
- O mercado se divide em dois: "automatiza remediação" (Datadog/incident.io/
  PagerDuty/Azure/Aurora) e "framework/benchmark" (OpenSRE/SmythOS). Nós somos
  **consultivo-com-rigor-de-segurança**, com porta aberta para execução
  controlada.
- **Quando a execução entrar** (roadmap, em aberto): herdamos o pitch dos que
  agem, MAS com a defesa já madura e human-in-the-loop por design — não como
  remendo.

## Sinais para revisitar este documento

- Implementar a spec 14 → atualizar "lacuna #1" (segurança vira força real).
- Adotar benchmark de RCA → atualizar "lacuna #2".
- Decidir habilitar execução → revisar todo o posicionamento (deixa de ser
  "não age") e estender threat model (ver spec 14 / READ_ONLY_POLICY).
- Re-analisar concorrentes a cada ~trimestre (espaço evolui rápido).

## Pendências de análise

- ✅ **HolmesGPT** (Robusta/MS, CNCF sandbox) — analisado (deep dive de código,
  2026-06-16). É o peer mais maduro e mais alinhado conosco (read-only por
  design, respeita RBAC, multi-LLM via litellm incl. Bedrock). Achados-chave já
  incorporados na matriz e na tabela de ideias acima: context-window management
  (spill-to-disk + llm_summarize), `ApprovalRequirement` (HITL por-tool),
  `safeguards` anti-loop, toolsets YAML com prerequisites/transformers.
  **Notável**: nem o HolmesGPT tem guardrail anti-injection dedicado (só
  sanitização de params) — reforça que nossa spec 14 seria um diferencial real.
- Leitura **profunda de código** (não só README) de Aurora e OpenSRE — os dois
  próximos restantes — para extrair padrões concretos de implementação.

## HolmesGPT — deep dive (o peer de referência)

Por ser CNCF, mantido por Robusta + Microsoft, e o mais alinhado (read-only),
vale destacar o que aprendemos lendo o código (`holmes/core/`):

- **Stack**: FastAPI + Pydantic + **litellm** (abstração multi-LLM — OpenAI/
  Anthropic/Azure/**Bedrock**/Gemini). `ToolCallingLLM` com `max_steps` é o
  agentic loop.
- **Read-only por design + RBAC** — mesma tese nossa, mas com um **modelo de
  HITL pronto** (`ApprovalRequirement`) para quando precisarem de ação (ex:
  operator mode abrindo PRs via GitHub). É o caminho que descrevemos para nossa
  execução futura — eles já têm a estrutura.
- **Context management é diferencial deles** (e nossa dor atual): `tool_context_
  window_limiter` derrama resultado grande para disco, `llm_summarize`
  transformer resume output de tool com modelo rápido, jq-query paginado (batches
  de 500) evita overflow. Diretamente aplicável ao nosso MCP/adapters.
- **Segurança**: sanitização de params (`shlex.quote`), `safeguards` anti-loop,
  RBAC — mas **sem** detector de prompt-injection dedicado. Lacuna do líder =
  oportunidade nossa (spec 14).
- **Operator mode**: roda 24/7, detecta proativamente, avisa no Slack — é o
  "agentes proativos" do nosso roadmap, já maduro. Referência de UX.

---

## Deep-dive: padrões de engenharia para adotar (2026-06-16)

Achados de **leitura de código** (HolmesGPT, Aurora, OpenSRE) que são melhoria
concreta para nós. Ordenados por valor.

### 🔴 Alto valor — atacam nossos maiores gaps

1. **Benchmark scored de RCA — OpenSRE `tests/benchmarks/_framework/`** (o gap #2).
   Não é "um teste" — é um framework de avaliação científica de qualidade de RCA:
   - **CloudOpsBench**: corpus de **452 cenários** (HF dataset), não commitado no
     repo — baixado em runtime; mirror em S3 revision-pinned para runs em Fargate.
   - `cost.py`: **accounting de custo input/output separado por modelo + hard-cap
     budget** (`CostBudgetExceeded` halta o run e publica relatório parcial — não
     estoura silenciosamente). Casa 100% com nossa steering de eficiência.
   - `overfit.py`: **guards anti-overfitting** (uniformidade por sistema/categoria,
     held-out 80/20, A/A consistency de 2 seeds) — garante que uma "melhoria" não
     é só sorte concentrada num cluster de casos.
   - `provenance.py` + `integrity.py`: cada run grava code SHA, config, env,
     versões de modelo → reprodutível e auditável.
   - **Por que roubar**: hoje não medimos se a RCA é boa. Isso é o método. Vira
     candidato a **spec própria** (ex: `28-rca-benchmark`).

2. **Defesa anti-injection em camadas reais — Aurora `server/guardrails/`** (gap #1, alimenta spec 14).
   - `input_rail.py`: **NeMo Guardrails como pre-flight** ANTES do agente planejar
     — "compromised inputs never reach tool selection". Usa a variável estruturada
     `triggered_input_rail` (não string-match de recusa — imune a wording do modelo).
     **Fail-closed**: qualquer erro bloqueia. É exatamente o desenho da nossa spec 14,
     já implementado — referência direta.
   - `sigma_loader.py`: **transpila regras SigmaHQ → regex** para detectar comandos
     maliciosos (clear syslog, crypto mining, curl|wget exec /tmp, etc.). Subset
     curado (linux/process_creation, high/critical). **Camada L2 da nossa spec 14**,
     pronta para quando formos executar comandos.
   - `test_sigma_canary.py`: teste de **canary** garantindo que a defesa não regrediu.
   - **Por que roubar**: valida a spec 14 e dá implementação de referência. NeMo é
     alternativa/complemento ao Bedrock Guardrails (multi-provider via LangChain factory).

### 🟠 Médio valor — eficiência e robustez

3. **Context-window management — HolmesGPT `core/tools_utils/`** (alimenta steering de eficiência).
   - `tool_context_window_limiter.py`: **spill de resultado grande para disco**;
     só um resumo entra no contexto. `get_pct_token_count` dimensiona por % da
     janela do modelo.
   - `llm_summarize` transformer: resume output de tool com **modelo rápido** antes
     de injetar (tiering aplicado a output, não só a roteamento).
   - jq-query **paginado** (batches de 500) na toolset k8s — evita overflow na origem.
   - **Por que roubar**: é a solução direta para a dor que medimos (MCP trouxe 165KB).
     Ataca custo na raiz.

4. **`ApprovalRequirement` por-tool — HolmesGPT `core/tools.py`** (execução futura).
   Modelo `needs_approval` + `reason` + `prefixes_to_save` (aprovação de prefixo de
   comando bash reutilizável). É o human-in-the-loop granular que vamos precisar.

5. **`safeguards.prevent_overly_repeated_tool_call` — HolmesGPT** (eficiência + anti-abuso).
   Barra tool-call idêntica repetida — conté loop/custo sem ML. Trivial de portar.

### 🟢 Muito interessante (pertinente, não urgente)

6. **Toolsets YAML declarativos com `prerequisites` — HolmesGPT**. Um toolset
   declara o que precisa (`kubectl version --client`) e só ativa se o pré-requisito
   passa. Mais robusto que nossos adapters assumirem que a dependência existe.
   `expose_remotely: true` permite chamar toolset cluster-local via MCP cross-cluster.

7. **`litellm` como camada multi-LLM — HolmesGPT**. Um wrapper, N providers
   (OpenAI/Anthropic/Azure/Bedrock/Gemini) + prompt caching + content_filter
   finish_reason já tratado. Reavaliar vs. nosso boto3-bedrock direto (ADR-001) —
   trade-off: menos lock-in vs. mais uma dependência.

8. **Operator mode 24/7 — HolmesGPT**. Roda em background, detecta proativamente,
   avisa no Slack, abre PR (GitHub integration). É o "agentes proativos" do nosso
   roadmap, maduro — referência de UX/arquitetura.

9. **Modos training/shadow/detect — versus-incident**. Introduzir
   detecção/ação sem risco: `shadow` loga "would have alerted" sem alertar.
   Padrão de rollout seguro para quando formos agir.

### Candidatos a spec (derivados destes achados)
- `28-rca-benchmark` — suíte scored de qualidade de RCA (de OpenSRE; alto valor).
- Reforço da `14-security-hardening` com NeMo input rail + Sigma rules (de Aurora).
- Spec/feature de **context budgeting** universal nos adapters (de HolmesGPT;
  alimenta steering de eficiência).
