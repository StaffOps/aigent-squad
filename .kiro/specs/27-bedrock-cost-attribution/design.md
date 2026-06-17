# Design: Bedrock Cost Attribution

## Arquitetura

```
                  ┌─────────────────────────────┐
                  │  AWS Billing / Cost Explorer │  $ autoritativo POR MODELO
                  └──────────────▲──────────────┘
                                 │ cost allocation tags
                  ┌──────────────┴──────────────┐
                  │ Application Inference Profile │  (1 por modelo)
                  │  tags: CostProject/Scope/...  │
                  └──────────────▲──────────────┘
                                 │ modelId = AIP ARN
   agent ──invoke(model=aip)──▶ BedrockClient ──emit──▶ aigent.tokens.total
                                                         {model, agent_id, direction}
                                                              │
                              rateio = tokens(agente,modelo) / tokens(modelo)
```

## Componentes

| Componente | Responsabilidade |
|-----------|-----------------|
| `bedrock-aip/` (Terraform) | Cria AIP por modelo + tags; output mapa modelo→ARN |
| `iam/` (Terraform) | Permite invoke em `application-inference-profile/*` |
| `BedrockClient.invoke` | Aceita `agent_id`; labela métricas de token/custo |
| `GenericAgent` / `Classifier` | Passam seu `agent_id` ao invocar |

## Rationale (decisões)

### Decisão 1: AIP por MODELO, não por agente×modelo

**Escolha**: 1 Application Inference Profile por modelo; rateio por agente vem
da métrica de tokens (`agent_id` label), não de um AIP dedicado por agente.

**Justificativa, em ordem de força**:
1. **Custo por agente é problema de rateio (showback), não de infra.** A AWS
   dá o $ autoritativo por modelo (tag); a proporção por agente sai da
   telemetria que já temos. Não precisa de N×M recursos pra isso.
2. **Evita recurso morto.** Hoje há 1 modelo e cada agente usa o mesmo. AIP
   por agente×modelo criaria 6+ perfis que cobram a mesma coisa — complexidade
   sem ganho.
3. **Flexibilidade de refatiar.** Rateio por métrica permite cortar por agente
   HOJE e por sessão/tenant DEPOIS, sem tocar em infra.

**Trade-offs aceitos**:
| Custo | Realidade |
|-------|-----------|
| Custo por agente é *estimado* (rateio), não cobrado diretamente | O total bate com a fatura; o rateio é proporcional ao consumo real de tokens — preciso o suficiente para showback |
| Depende da telemetria estar funcionando | Métrica já existe; só falta o label `agent_id` |

**Quando estaria errada** (signal): se for preciso **cobrança contratual**
(chargeback real, não showback) por agente/tenant — aí AIP dedicado por
dimensão cobrável passa a valer. Para showback interno, rateio basta.

### Decisão 2: `copy_from` aponta para o system inference profile (`us.`)

**Escolha**: `model_source.copy_from = arn:...:inference-profile/us.<model>`,
não o `foundation-model/<model>`.

**Justificativa**:
1. O modelo (Claude Sonnet 4.5) **exige** inference profile (não suporta
   on-demand no foundation-model ARN — confirmado empiricamente:
   `ValidationException: on-demand throughput isn't supported`).
2. O profile `us.` dá cross-region (us-east-1/2, us-west-2) — resiliência.

**Trade-off**: o AIP herda o roteamento cross-region do profile-fonte (ok).

### Decisão 3: Rateio por proporção de tokens (não por contagem de chamadas)

**Escolha**: rateio = tokens do agente / tokens totais do modelo.

**Justificativa**: o Bedrock cobra por token, não por chamada. Uma chamada de
RCA com 165KB de contexto custa muito mais que um "oi". Ratear por nº de
chamadas distorceria; por tokens espelha o billing real.

**Refinamento**: input e output têm preços diferentes (~$3 vs $15/milhão). O
rateio mais fiel pondera input/output pelos respectivos preços. A métrica
`aigent.cost.estimated` (já calculada com esses pesos) labelada por `agent_id`
resolve isso diretamente — é a melhor chave de rateio.

## Invariantes

- O total ratereado por agente SHALL somar ao custo do modelo na AWS.
- `agent_id` em métrica é bounded (nº de agentes ~6) — seguro como label
  (não viola cardinalidade, ao contrário de `user_id`).

## Fórmula de rateio (operacional)

```
# Proporção de custo estimado por agente, por modelo (MetricsQL)
sum by (agent_id) (aigent_cost_estimated{model="<aip_arn>"})
  / ignoring(agent_id) group_left
sum (aigent_cost_estimated{model="<aip_arn>"})

# Aplicar à fatura real do Cost Explorer:
# custo_real_agente = proporcao_acima × custo_modelo_no_cost_explorer
```

## Dependências externas

| Serviço | Propósito |
|---------|-----------|
| AWS Bedrock (AIP) | Tag de custo no billing record |
| Cost Explorer / CUR | $ autoritativo por tag |
| VictoriaMetrics | Métrica de tokens/custo por agent_id (rateio) |
| AWS Billing console | Ativar cost allocation tags (manual, 1x) |
