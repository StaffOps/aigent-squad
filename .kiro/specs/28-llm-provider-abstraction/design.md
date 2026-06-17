# Design: LLM Provider Abstraction

## Arquitetura

```
        GenericAgent / Classifier
                  │  invoke(messages, system_prompt, ..., agent_id)
                  ▼
        ┌─────────────────────────────┐
        │  LLMService (camada comum)   │  ← circuit breaker, retry/backoff,
        │                             │     métricas token/custo {model,agent_id},
        │                             │     prompt-caching policy (spec 11)
        └──────────────┬──────────────┘
                       │  delega transporte para
            ┌──────────▼───────────┐
            │   LLMProvider (Protocol) │
            └──────────┬───────────┘
        ┌──────────────┼───────────────┐
        ▼              ▼               ▼
 BedrockProvider   (futuro)        (futuro)
 (boto3, hoje)     LiteLLMProvider  AnthropicProvider
```

**Princípio**: o que é **transversal** (resiliência, métricas, custo) fica na
`LLMService` — UMA vez, vale para todos os providers. O `LLMProvider` só faz o
**transporte** (montar request, chamar, parsear resposta → texto + usage).

## Interface (rascunho)

```python
class LLMProvider(Protocol):
    async def complete(
        self,
        messages: list[dict],
        system_prompt: str,
        max_tokens: int,
        temperature: float,
        model: str,            # model id OU ARN do AIP (Bedrock)
    ) -> LLMResult: ...        # texto + usage(input/output tokens) + model real
```

`LLMResult` carrega `input_tokens`/`output_tokens`/`model` para a `LLMService`
emitir as métricas (preserva spec 27). O `agent_id` é label aplicado pela
`LLMService`, não responsabilidade do provider.

## Rationale (decisões)

### Decisão 1: abstração própria com providers plugáveis (não litellm no core)

**Escolha**: introduzir `LLMProvider` (Protocol) nossa; `litellm`, se adotado,
é UMA implementação por baixo — não o substituto do `BedrockClient`.

**Justificativa, em ordem de força**:
1. **Preserva o que construímos**: circuit breaker, retry, e — crítico —
   métricas de custo por `agent_id` (spec 27) e cost-attribution via AIP. Se o
   litellm virasse o core, teríamos que re-cabear isso nos callbacks dele
   (sistema de cost-tracking próprio, diferente do nosso).
2. **Desacopla sem comprometer com uma lib**: a interface vale mesmo se nunca
   adotarmos litellm (poderíamos escrever `AnthropicProvider` à mão).
3. **Refactor seguro**: `BedrockProvider` = código atual movido, comportamento
   idêntico, mesmos testes.

**Trade-offs aceitos**:
| Custo | Realidade |
|-------|-----------|
| Mais uma camada (LLMService + Provider) | Pequena; isola transporte de política |
| Não "ganhamos litellm de graça" no core | De propósito — o core é nosso |

**Quando estaria errada**: se a manutenção de múltiplos providers à mão crescer
muito, litellm-no-core passa a valer (mas aí re-cabeando métricas
conscientemente).

### Decisão 2: litellm é candidato, não pré-requisito

**Escolha**: a abstração vem primeiro (Decisão 1). litellm vs cliente à mão é
decisão da SEGUNDA implementação, separada.

**Justificativa**: o passo 1 (extrair interface) já entrega desacoplamento e é
risco baixo. Escolher a 2ª implementação sem pressa permite avaliar litellm de
verdade (licença MIT — dependência OK; maturidade; overhead).

### Decisão 3: cost-attribution é invariante a preservar (não regredir)

**Escolha**: qualquer provider DEVE permitir que `LLMService` emita
`tokens{model,agent_id,direction}` e `cost{model,agent_id}`.

**Justificativa**: spec 27 é a base do FinOps do produto. Trocar transporte não
pode cegar o custo. **Armadilha conhecida**: litellm tem cost-tracking próprio;
se delegássemos a ele, perderíamos o label `agent_id`. Por isso métrica fica na
`LLMService`, alimentada pelo `usage` que o provider retorna.

## Invariantes

- Métricas de custo por `agent_id`+`model` NUNCA regridem (spec 27).
- Cost-attribution via AIP (Bedrock) continua: ARN do AIP entra como `model`.
- `BedrockProvider` mantém comportamento idêntico ao `BedrockClient` atual.
- Resiliência (circuit breaker/retry) é da `LLMService`, não duplicada.

## Licenciamento (clean-room)

- Implementação **do zero**; nenhuma cópia de código de HolmesGPT/Aurora/etc.
- litellm (se adotado) = dependência declarada (MIT), não cópia de source.
- Inspiração conceitual (ex: "provider plugável") é livre; expressão não se copia.

## Dependências externas (potenciais)

| Serviço/lib | Propósito | Licença |
|-------------|-----------|---------|
| boto3 | Bedrock (atual) | Apache-2.0 (já em uso) |
| litellm (candidato) | multi-provider transport | MIT (verificar na adoção) |
