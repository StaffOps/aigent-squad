# Tasks: Multi-Agent Collaboration

> Pré-requisito: `06-resilience-patterns` (async-first) e `09-otel-instrumentation` (propagação de trace) implementadas. Sem async real, o fan-out não paraleliza.

- [ ] T1: Estender `ClassifierResult` para `agents: list[AgentMatch]` + propriedade compat `selected_agent`; atualizar prompt do classifier p/ 1..N agentes + `max_agents` (default 3)
- [ ] T2: Atualizar parsing do classifier p/ lista (robusto: JSON em markdown, truncado, agente desconhecido filtrado) (depends on: T1)
- [ ] T3: Implementar `_fan_out` no supervisor com `asyncio.gather(..., return_exceptions=True)` + tolerância a falha parcial (depends on: T1)
- [ ] T4: Criar `src/supervisor/synthesizer.py` — 1 chamada Bedrock (Sonnet) que funde N respostas → 1 com atribuição + nota de degradação (depends on: T3)
- [ ] T5: Wire no `process_request`: N=1 → fast-path atual; N≥2 → `_fan_out` → `synthesizer` (depends on: T3, T4)
- [ ] T6: Hop guard — header `X-Agent-Hop` no contrato `/process`; rejeitar `≥2`; propagar nos agentes (depends on: —)
- [ ] T7: Criar `src/core/agent_tools.py` — cliente p/ um agente chamar outro (via supervisor, hop=1) + expor como ferramenta opcional no `agent_base` (depends on: T6)
- [ ] T8: Métricas/trace: span único supervisor→N→síntese; métricas `fanout_size`, `synthesis_calls`, `partial_failures` (depends on: spec 09)
- [ ] T9 (test-author DIFERENTE do autor): testes pytest ≥90% — classifier multi, fan-out paralelo (assert tempo≈max), síntese, falha parcial, `X-Agent-Hop=2` rejeitado, fast-path N=1 sem síntese (depends on: T5, T6, T7)
- [ ] T10: Review independente (`code-review`): valida contrato, anti-ciclo, custo (max_agents aplicado antes das chamadas), read-only preservado (depends on: T9)
- [ ] T11: Build + smoke via Docker: 1 query single-domain (fast-path) + 1 cross-domain (fan-out+síntese) (depends on: T10)

## Ordem sugerida
T1→T2; T6 em paralelo; T3→T4→T5; T7 (após T6); T8 (após spec 09); T9→T10→T11.

## Notas
- Pipeline de verificação (steering `verification-independence.md`): T1–T8 = autor; T9 = test-author em sessão diferente; T10 = code-review.
- N=1 é caminho crítico — não pode regredir em latência nem custo (sem síntese).
- Custo é o maior risco: `max_agents` é gate duro, não recomendação.
- Não reintroduzir LangGraph (steering `project.md`) — orquestração é código próprio no supervisor.
