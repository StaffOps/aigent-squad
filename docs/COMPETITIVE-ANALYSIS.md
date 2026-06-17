# Competitive Analysis — AI SRE / Incident-RCA Agents

**Última atualização**: 2026-06-16
**Método**: leitura de READMEs, configs e estrutura de 6 projetos open-source em
`example-sres/` + produtos comerciais (Datadog Bits, incident.io, PagerDuty,
Azure SRE Agent) via material público. **Não** é leitura profunda do código de
cada repo (READMEs + estrutura). HolmesGPT pendente de análise.

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

- **HolmesGPT** (robusta-dev) — não analisado (clone só com `.git`). É um dos
  players open-source mais relevantes; analisar e adicionar à matriz.
- Leitura **profunda de código** (não só README) de Aurora e OpenSRE — os dois
  mais próximos — para extrair padrões concretos de implementação.
