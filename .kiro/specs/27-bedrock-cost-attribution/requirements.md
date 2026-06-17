# Feature: Bedrock Cost Attribution (AIP por modelo + rateio por agente)

## Objetivo

Saber **quanto cada agente custou em Bedrock**, com custo total autoritativo
(que bate com a fatura AWS) e rateio por agente vindo da telemetria.

## Abordagem (showback)

```
AWS (autoritativo)   : Application Inference Profile por MODELO + cost
                       allocation tags → Cost Explorer/CUR dá $ por modelo
App (chave de rateio): métrica de tokens labelada por {model, agent_id}
                       → proporção de consumo de cada agente
─────────────────────────────────────────────────────────────────────
custo(agente, modelo) = (tokens do agente no modelo / total do modelo)
                        × ($ do modelo na AWS)
```

## User Stories

WHEN um agente invoca o Bedrock
THEN a chamada SHALL usar o ARN do Application Inference Profile do modelo
(não o model ID puro), para que o custo carregue as cost allocation tags.

WHEN a métrica de tokens é emitida
THEN ela SHALL ser labelada com `agent_id` E `model`, para permitir rateio
por agente via query.

WHEN o operador consulta custo no Cost Explorer
THEN o custo de Bedrock SHALL ser filtrável pelas tags `CostProject`,
`CostScope`, `Environment`, `CostCenter`.

## Acceptance Criteria

- [ ] Módulo Terraform cria 1 AIP por modelo, com as 4 tags FinOps.
- [ ] AIP usa `model_source.copy_from` apontando para o **system inference
      profile** (`us.`) — necessário para cross-region.
- [ ] Output: mapa `{model_key: aip_arn}` consumível pela app/Helm.
- [ ] Policy IAM permite `bedrock:InvokeModel` no ARN
      `application-inference-profile/*` da conta.
- [ ] Métrica `aigent.tokens.total` e `aigent.cost.estimated` labeladas com
      `agent_id` (além de `model`, `direction`).
- [ ] Cobertura de testes ≥90% na mudança da app.
- [ ] Doc: pré-requisito manual (ativar cost allocation tags no Billing) +
      query MetricsQL de rateio.

## Tags (confirmadas pelo usuário)

| Tag | Valor |
|-----|-------|
| `CostProject` | `aigent-squad` |
| `CostScope` | `MONITORING` |
| `Environment` | `PRD` |
| `CostCenter` | **variável (sem default)** — obrigatória, passada no apply |

## Fora de escopo

- AIP por agente×modelo (matriz) — desnecessário; rateio por agente vem da
  métrica, não de infra dedicada (ver Rationale no design).
- Atribuição por usuário/sessão (cardinalidade — via traces/logs, não métrica).
- Tiering de modelo real (Haiku no classifier) — isso é a spec 11; este
  módulo só fica preparado para múltiplos modelos.
