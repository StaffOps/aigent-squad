# Tasks: Bedrock Cost & Model Tiering

> Depende da 06 (bedrock async). Modelo por papel vem do config (19/22).

- [x] T1: Model resolver em `bedrock.py` — papel (`classifier`/`agent`/`synthesis`) → modelo do config
- [x] T2: Classifier passa a usar o modelo `classifier` (Haiku) (depends on: T1)
- [x] T3: Reabilitar prompt caching (`cache_control: ephemeral`) + validar suporte no startup, degradar se indisponível (depends on: T1)
- [x] T4: Token budget por sessão (hard cap configurável) + corte com mensagem clara
- [x] T5: Truncamento de histórico por tokens (não por contagem de mensagens) (depends on: T4)
- [x] T6: Emitir métrica custo/token (input/output por agente+modelo) — hook p/ spec 10
- [x] T7 (test-author DIFERENTE do autor): pytest ≥90% — modelo por papel, caching no body, budget corta, truncamento (depends on: T1–T5)
- [x] T8: Review independente (`code-review` + `finops`): tiering correto, caching efetivo, budget hard cap (depends on: T7)

## Status (2026-07-02)
Implementado via pipeline (dev → test-author → code-review + finops), 100% cobertura em `model_tier.py` + `token_budget.py` (62 testes). Remediações aplicadas:
- **finops**: pricing Haiku corrigido p/ 4.5 (`$1/$5/$0.10`); `compute_cost` agora precifica cache-WRITE a 1.25x (além de cache-read a 0.1x); `cache_creation_input_tokens` passado do `bedrock.py`.
- **code-review**: budget tracker WIRED no fluxo (era inerte) — `record_usage` em cada call Bedrock (`bedrock._invoke_sync`), `check_budget` (hard cut) no `SupervisorAgent.process_request`. Enricher mantido em Sonnet (tier `synthesis`) com Opus documentado como promotion trigger (design.md), não deferral silencioso.
- Model IDs por papel: `bedrock_classifier_model_id` (Haiku), `bedrock_agent_model_id`/`bedrock_synthesis_model_id` (Sonnet) em `config.py`. ⚠️ operador deve confirmar o inference-profile exato do Haiku na conta.

## Ordem sugerida
T1→T2/T3; T4→T5; T6; T7→T8.

## Notas
- Não é "economizar demais": é não desperdiçar no roteamento + cortar latência (Haiku) sem perder qualidade na resposta (Sonnet).
- Cross-region failover fora de escopo (over-engineering pré-MVP).
- Pipeline de verificação (`verification-independence.md`): T1–T6 autor; T7 test-author; T8 code-review.
