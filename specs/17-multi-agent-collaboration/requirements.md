# Feature: Multi-Agent Collaboration

**Spec**: `17-multi-agent-collaboration`
**Severidade**: 🟢 Feature (capability nova, não correção)
**Origem**: `../ANALYSIS.md` → "Comportamentos novos" (agent-as-tools + execução paralela)
**Depende de**: `06-resilience-patterns` (async-first — sem ela o fan-out serializa), `09-otel-instrumentation` (trace pra enxergar o grafo de chamadas)

Hoje o sistema é **hub-and-spoke**: o classifier escolhe **exatamente 1** agente por query e queries cross-domain caem em `"unknown"`. Esta spec adiciona colaboração: o supervisor pode acionar **N agentes em paralelo** quando a query toca múltiplos domínios e **sintetizar** uma resposta única; e um agente pode pedir dado a outro (**agent-as-tools**) quando precisa de contexto fora do seu domínio.

## User Stories

WHEN a query do usuário toca múltiplos domínios (ex: "por que meu custo AWS subiu após o último deploy no k8s?") THEN o classifier SHALL retornar uma lista ordenada de agentes relevantes (1..N), não um único.

WHEN o classifier seleciona N≥2 agentes THEN o supervisor SHALL chamá-los **em paralelo** (não sequencial) e sintetizar as respostas em uma única resposta coerente.

WHEN N=1 THEN o comportamento SHALL ser idêntico ao atual (sem overhead de síntese).

WHEN um agente precisa de dado de outro domínio para responder THEN ele SHALL poder requisitar esse dado a outro agente via uma chamada de ferramenta controlada (agent-as-tools), com profundidade máxima de 1 salto.

WHEN a síntese é feita THEN o supervisor SHALL preservar atribuição (qual agente disse o quê) e a política read-only.

WHEN qualquer agente no fan-out falha ou estoura timeout THEN o supervisor SHALL sintetizar com as respostas disponíveis e sinalizar a degradação (não falhar a query inteira).

## Acceptance Criteria

- [ ] Classifier retorna `agents: [{agent, confidence, reasoning}]` ordenado por relevância (contrato novo, retrocompatível com `selected_agent`).
- [ ] Supervisor executa fan-out **concorrente** (`asyncio.gather`) para N≥2 agentes; tempo total ≈ max(latência dos agentes), não soma.
- [ ] Etapa de síntese: 1 chamada Bedrock que recebe as N respostas + a query e produz a resposta final com atribuição.
- [ ] N=1 não dispara síntese (fast-path inalterado).
- [ ] Agent-as-tools: um agente pode chamar **≤1** outro agente; recursão/ciclo bloqueado por `hop` header (max depth = 1).
- [ ] Fan-out tolera falha parcial: 1 agente caído → resposta sai com os demais + nota de degradação.
- [ ] Limite duro: máximo de agentes paralelos por query configurável (default 3) — proteção de custo.
- [ ] Trace único atravessa supervisor → N agentes → síntese (propagação de contexto — depende da spec 09).
- [ ] Testes (autor ≠ test-author, ≥90%): classifier multi-agente, fan-out paralelo, síntese, falha parcial, bloqueio de ciclo, fast-path N=1.

## Fora de escopo

- Streaming da resposta sintetizada (futuro; `agent_base` já tem `AsyncIterable`).
- Debate/negociação entre agentes (round-trips múltiplos) — esta spec faz **1 rodada** de fan-out + síntese.
- Profundidade de agent-as-tools > 1 salto (explicitamente proibida por custo/latência/ciclo).
- Mudança no isolamento de histórico por agente (spec 02 mantém).
