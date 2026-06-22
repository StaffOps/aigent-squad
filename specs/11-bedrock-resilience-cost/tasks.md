# Tasks: Bedrock Cost & Model Tiering

> Depende da 06 (bedrock async). Modelo por papel vem do config (19/22).

- [ ] T1: Model resolver em `bedrock.py` — papel (`classifier`/`agent`/`synthesis`) → modelo do config
- [ ] T2: Classifier passa a usar o modelo `classifier` (Haiku) (depends on: T1)
- [ ] T3: Reabilitar prompt caching (`cache_control: ephemeral`) + validar suporte no startup, degradar se indisponível (depends on: T1)
- [ ] T4: Token budget por sessão (hard cap configurável) + corte com mensagem clara
- [ ] T5: Truncamento de histórico por tokens (não por contagem de mensagens) (depends on: T4)
- [ ] T6: Emitir métrica custo/token (input/output por agente+modelo) — hook p/ spec 10
- [ ] T7 (test-author DIFERENTE do autor): pytest ≥90% — modelo por papel, caching no body, budget corta, truncamento (depends on: T1–T5)
- [ ] T8: Review independente (`code-review`/`finops`): tiering correto, caching efetivo, budget hard cap (depends on: T7)

## Ordem sugerida
T1→T2/T3; T4→T5; T6; T7→T8.

## Notas
- Não é "economizar demais": é não desperdiçar no roteamento + cortar latência (Haiku) sem perder qualidade na resposta (Sonnet).
- Cross-region failover fora de escopo (over-engineering pré-MVP).
- Pipeline de verificação (`verification-independence.md`): T1–T6 autor; T7 test-author; T8 code-review.
