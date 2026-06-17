# Tasks: LLM Provider Abstraction

Status: **design only**. Não implementar sem decisão explícita de priorizar.

## Phase 1 — Abstração (refactor seguro, valioso sozinho)
- [ ] Task 1: Definir `LLMProvider` (Protocol) + `LLMResult` (texto + usage + model)
- [ ] Task 2: `LLMService` (camada comum): mover circuit breaker, retry/backoff,
      e emissão de métricas {model, agent_id, direction} para cá
- [ ] Task 3: `BedrockProvider` = `BedrockClient` atual refatorado, comportamento
      idêntico (mesmos testes de `test_bedrock.py` passam)
- [ ] Task 4: Callers (GenericAgent, Classifier) usam `LLMService`; seleção por env
- [ ] Task 5: Validar cost-attribution (spec 27) + AIP ARN ainda funcionam
- [ ] Task 6: Testes ≥90%; ADR-001 atualizada/nova ADR

## Phase 2 — Segundo provider (decisão separada)
- [ ] Task 7: Decidir litellm (dep MIT) vs `AnthropicProvider` à mão — avaliar
      maturidade, overhead, licença, impacto em métricas
- [ ] Task 8: Implementar o provider escolhido como `LLMProvider`
- [ ] Task 9: Testes de paridade (mesmo contrato, ambos providers)

## Notas
- **Clean-room**: implementação do zero; litellm só como dependência se escolhido.
- **Não é orquestração**: transporte de LLM só. LangGraph/Strands seguem fora (ADR-001).
- Armadilha: não delegar cost-tracking ao litellm (perderia label agent_id).
