# Feature: Bedrock Cost & Model Tiering

**Spec**: `11-bedrock-resilience-cost`
**Severidade**: 🟠 High (custo + latência do roteamento)
**Origem**: `../ANALYSIS.md` CONV-3, finops F1/F2, aws F11
**Depende de**: `06-resilience-patterns`

Toda query faz **2 chamadas Bedrock** (classifier + agente), ambas com Sonnet 4.5. Usar Sonnet para *roteamento* é ~10–13× mais caro que o necessário, e o **prompt caching está desligado** (`bedrock.py:28-29` comentado), reenviando ~6400 tokens de system prompt a cada chamada. Esta spec aplica **model tiering** (modelo certo por papel) + **prompt caching** + budget. Não é "economizar demais" — é não desperdiçar no roteamento e cortar latência onde dá.

## User Stories

WHEN o classifier roteia THEN SHALL usar um modelo **rápido/barato** (Haiku) — roteamento é tarefa simples.

WHEN um agente responde / a RCA sintetiza THEN SHALL usar um modelo **forte** (Sonnet) — qualidade importa.

WHEN o mesmo system prompt grande é reenviado THEN SHALL usar **prompt caching** do Bedrock (desconto ~90% no input cacheado).

WHEN uma sessão consome muitos tokens THEN SHALL respeitar um **budget** configurável (corta antes de explodir custo).

WHEN o modelo por papel é escolhido THEN SHALL vir do **config** (`model_tier` da spec 22 / config da 19), não hardcoded.

## Acceptance Criteria

- [ ] Modelo por papel configurável: `classifier`→Haiku, `agent`/`synthesis`→Sonnet (alinha specs 19/22).
- [ ] Prompt caching reabilitado (`cache_control: ephemeral` no system block) + validado contra o modelo.
- [ ] Budget de tokens por sessão (hard cap configurável) — corta com mensagem clara.
- [ ] Truncamento de histórico por tokens (não só por contagem de mensagens).
- [ ] Métricas de custo/token emitidas (input/output por agente+modelo) — coordena com observability (futura spec 10).
- [ ] Testes (test-author ≠ autor, ≥90%): seleção de modelo por papel, caching aplicado, budget corta, truncamento.

## Fora de escopo
- Cross-region failover de Bedrock — futuro (over-engineering pré-MVP).
- Dashboards de custo → spec 10 (observability).
- Re-treino/avaliação de qualidade do Haiku no roteamento — validar empiricamente em uso.
