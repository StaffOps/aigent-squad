# Feature: LLM Provider Abstraction (multi-provider layer)

**Spec**: `28-llm-provider-abstraction`
**Status**: 📝 design only — não implementar sem decisão explícita
**Depende de**: `ADR-001` (reabre a decisão "Bedrock direto, sem framework")
**Relacionado**: `27-bedrock-cost-attribution`, `11-bedrock-resilience-cost`,
`steering/efficiency-cost.md`

---

## Objetivo

Permitir que o AIgent-squad use **múltiplos provedores de LLM** (Bedrock hoje;
Anthropic direto, OpenAI, Gemini no futuro) atrás de uma interface única, sem
reescrever os agentes nem perder o que já construímos (circuit breaker, retry,
métricas de custo por agente, cost-attribution via AIP).

Hoje o `src/core/bedrock.py::BedrockClient` é a única implementação, acoplada ao
boto3 + formato Anthropic-on-Bedrock. Os callers (GenericAgent, Classifier)
chamam `bedrock.invoke(...)` diretamente.

## ⚠️ Licenciamento (clean-room — MANDATÓRIO)

Esta abstração pode ser inspirada em padrões de projetos open-source estudados
(HolmesGPT usa `litellm`), MAS:
- **NÃO copiar código** de nenhum repo de terceiro (Apache-2.0/MIT/etc.).
  Copyright protege a expressão, não a ideia. Implementação é **do zero**.
- Se adotar `litellm`, é **dependência declarada** (via package manager,
  licença respeitada no nível de dependência) — não cópia de source.
- Qualquer dependência nova tem a licença verificada e declarada antes de adotar.

## User Stories

WHEN um agente invoca o LLM THEN ele SHALL usar uma interface `LLMProvider`
única, agnóstica de provedor, com a mesma assinatura de hoje
(`messages`, `system_prompt`, `max_tokens`, `temperature`, `agent_id`).

WHEN o provedor é trocado (via config/env) THEN os agentes NÃO SHALL precisar
de alteração de código.

WHEN qualquer provedor é usado THEN circuit breaker, retry, e **métricas de
token/custo labeladas por `agent_id` + `model`** SHALL continuar funcionando
(preservar spec 27).

WHEN o provedor é Bedrock com Application Inference Profile THEN a
cost-attribution via AIP SHALL continuar funcionando (o ARN do AIP passa como
identificador de modelo).

## Acceptance Criteria

- [ ] Interface `LLMProvider` (Protocol) com `invoke(...)` — assinatura atual.
- [ ] `BedrockProvider` = refactor do `BedrockClient` atual, **comportamento
      idêntico** (mesmos testes passam sem mudança de expectativa).
- [ ] Circuit breaker, retry e métricas vivem na **camada comum** (acima do
      provider), não duplicados por implementação.
- [ ] Seleção de provider por env var (`LLM_PROVIDER=bedrock|...`).
- [ ] Segundo provider é decisão separada (litellm vs cliente à mão — ver design).
- [ ] Cost-attribution (spec 27) validada com o provider selecionado.
- [ ] Cobertura ≥90% no código novo; testes do `BedrockProvider` = os atuais.
- [ ] ADR-001 atualizada (ou ADR nova) registrando a mudança e o trade-off.

## Fora de escopo

- Implementar N providers de uma vez — começar pela abstração + Bedrock; o
  segundo provider é entrega separada.
- Orquestração de agentes por framework (LangGraph/Strands) — segue rejeitado
  pela ADR-001; esta spec é só **transporte de LLM**, não orquestração.
