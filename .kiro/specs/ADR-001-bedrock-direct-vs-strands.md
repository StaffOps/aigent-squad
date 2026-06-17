# ADR-001 — Bedrock direto vs. framework de agentes (Strands)

**Status**: Aceito
**Data**: 2026-06-16
**Contexto de decisão**: orquestração multi-agente do AIgent-squad
**Relacionado**: `02-unify-agent-architecture`, `17-multi-agent-collaboration`, `18-rca-investigation-workflow`, steering `project.md` ("não reintroduzir LangGraph")

---

## Decisão

**Manter a orquestração sobre Bedrock direto (classifier + GenericAgent +
investigation/synthesizer), sem adotar o AWS Strands Agents SDK no momento.**

---

## Contexto

O Strands Agents (SDK open-source da AWS, GA 1.0 em jul/2025) foi avaliado
como alternativa à orquestração atual. O Strands é **model-driven**: o LLM
decide autonomamente, em loop, quais tools chamar — em vez de o workflow ser
codado à mão. O 1.0 oferece 4 padrões multi-agente (agents-as-tools, swarm,
graph, workflow) e integra com Bedrock AgentCore (runtime/memory/observability
gerenciados).

O projeto **já migrou para fora de um framework antes** (LangGraph removido em
favor de Bedrock direto — registrado como proibição na steering). A pergunta é
se o Strands justifica reverter essa direção.

### Estado atual (o que já existe e está validado por testes)

| Capacidade | Implementação atual |
|-----------|---------------------|
| Roteamento | `classifier` (chamada Bedrock) → especialista |
| Especialistas | `GenericAgent` + `AGENTS_DIR` (config-driven, `agent.yaml`) |
| Fan-out + síntese | `investigation.py` + `synthesizer.py` |
| Tool-calling | Adapters **read-only** manuais (`adapters.py`) |
| Resiliência | `circuit_breaker.py`, cache sha256 determinístico, fail-open |
| Observability | OTel instrumentado |
| Read-only é lei | 4 camadas: prompt, IAM deny, RBAC, templates de recusa |

---

## Justificativa (em ordem de força)

1. **"Read-only é lei" briga com o paradigma model-driven.** O núcleo do
   Strands é o LLM decidir autonomamente quais tools invocar num loop. O
   projeto construiu deliberadamente o oposto: roteamento por classifier,
   adapters que só leem, 4 camadas de recusa de escrita. Adotar o tool-loop
   autônomo obrigaria a reconstruir essas barreiras *por cima* de um framework
   cujo propósito é remover exatamente esse controle.

2. **O "difícil" já está feito e testado.** Circuit breaker, cache
   determinístico, fail-open no DynamoDB, fan-out, synthesizer e OTel já
   existem com ~85% de cobertura. O ganho imediato do Strands (orquestração +
   tool loop) é justamente o que já foi codado e validado.

3. **O custo de sair de um framework já foi pago uma vez (LangGraph).** Voltar
   a acoplar a um framework agora arrisca repetir o mesmo ciclo de migração. A
   direção registrada na steering é controle direto sobre o Bedrock.

---

## Trade-offs aceitos

| Custo | Realidade |
|-------|-----------|
| Mantemos à mão orquestração que o Strands daria de graça (classifier, fan-out, synthesizer) | Já está escrito, testado e estável — custo marginal de manutenção é baixo |
| Não usamos padrões prontos (swarm/graph) nem AgentCore (runtime/memory gerenciados) | Não são necessários enquanto os agentes forem consultivos read-only single/poucos-rounds |
| Ficamos "fora" do caminho recomendado pela AWS para agentes | Lock-in zero e alinhamento total com a invariante read-only compensam |

---

## Quando esta decisão estaria errada (signals para reabrir)

- **Os agentes deixarem de ser consultivos read-only** e passarem a executar
  ações multi-step autônomas (encadear tools dinamicamente, planning de
  múltiplos passos). Aí classifier + investigation + synthesizer manuais viram
  peso morto e o tool-loop + swarm/graph do Strands passam a **habilitar** o
  que ainda não temos, em vez de competir com o que já temos.
- **Necessidade de runtime/memory gerenciados** (não querer operar isso no
  EKS) → Bedrock AgentCore passa a fazer sentido.
- **A orquestração manual crescer a ponto de a manutenção superar** o custo de
  adotar um framework (ex: muitos padrões de coordenação novos por trimestre).

---

## Alternativas consideradas e descartadas

- **Strands Agents SDK (self-hosted no EKS)** — descartado agora: paradigma
  model-driven conflita com read-only; reconstruiria barreiras de escrita por
  cima do framework.
- **Strands + Bedrock AgentCore (gerenciado)** — descartado agora: resolve
  runtime/memory que já temos cobertos (EKS + DynamoDB + pgvector); introduz
  acoplamento sem ganho proporcional no caso consultivo atual.
- **Reintroduzir LangGraph** — proibido por steering (`project.md`).
