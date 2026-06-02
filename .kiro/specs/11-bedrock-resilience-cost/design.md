# Design: Bedrock Cost & Model Tiering

## Arquitetura

Camada fina sobre o `bedrock.py` (já async pela spec 06): escolhe modelo por papel, liga caching, aplica budget.

```
papel (classifier|agent|synthesis) ─▶ model_tier (config 19/22) ─▶ modelo Bedrock
system prompt grande ─▶ cache_control: ephemeral ─▶ ~90% desconto no input repetido
sessão ─▶ token budget (hard cap) ─▶ corta antes de explodir
```

## Componentes

| Componente | Responsabilidade | Onde |
|-----------|------------------|------|
| Model resolver | papel → modelo (do config) | `src/core/bedrock.py` |
| Prompt cache | `cache_control: ephemeral` no system block | `src/core/bedrock.py` |
| Token budget | hard cap por sessão + truncamento por tokens | `src/core/bedrock.py` / classifier |
| Cost emit | métrica input/output por agente+modelo | `src/core/bedrock.py` (hook p/ spec 10) |

## Custo (ordem de grandeza, @200 queries/dia — do `ANALYSIS.md`)

| Item | Atual | Com esta spec |
|------|-------|---------------|
| Classifier (Sonnet→Haiku) | ~$24/mês | ~$2/mês |
| System prompt sem caching | ~$103/mês desperdiçado | ~$10/mês |
| **Efeito combinado** | — | **~$207→~$99/mês** |

## Decisões e trade-offs

### Decisão 1: Model tiering por camada (Haiku / Sonnet / Opus)

**Escolha**: cada camada do sistema usa o modelo mais custo-eficiente para sua complexidade.

**Tabela de tiering (config-driven, não hardcoded):**

| Camada | Modelo | Justificativa | Custo/chamada |
|--------|--------|---------------|---------------|
| Classifier (roteamento) | **Haiku** | Classificação simples (qual agente?). 1/13 do custo, menos latência. | ~$0.001 |
| Agentes coletores (evidência) | **Sonnet** | Query direcionada a datasource, raciocínio moderado. | ~$0.02 |
| Synthesizer Nível 1–2 (correlação) | **Sonnet** | Correlação com ≤5 rodadas de evidência. Suficiente. | ~$0.05 |
| Synthesizer Nível 3–4 (correlação complexa) | **Opus** | Correlação multi-rodada (10–25 rounds), raciocínio causal profundo. | ~$0.50 |
| Destilação extractor (draft KB) | **Sonnet** | Extrair fatos estruturados de investigação. Volume alto, qualidade OK. | ~$0.03 |
| Destilação enricher (refine KB) | **Opus** | Generalizar, encontrar padrões não-óbvios, escrever pra reuso futuro. Qualidade > velocidade. | ~$0.24 |

**Promotion triggers entre modelos:**
- Synthesizer Sonnet→Opus: RCA com confiança 'baixa' em >40% dos casos Nível 3+.
- Enricher Opus→Sonnet (demotion): Opus não adiciona valor mensurável em >60% das destilações (output ≈ input do Sonnet).

**Trade-off**: Haiku pode errar roteamento em query muito ambígua → mitigado pelo fallback do classifier (spec 06) e pelo fan-out multi-agente (spec 17) que cobre vários domínios.
**Quando reabrir**: se medições mostrarem queda de acurácia de roteamento com Haiku > limiar aceitável.

### Decisão 2: Reabilitar prompt caching
**Escolha**: ligar `cache_control: ephemeral` no system block (estava comentado "por compatibilidade").
**Justificativa**: o system prompt (~6400 tokens) é idêntico entre chamadas; caching dá ~90% de desconto no input cacheado dentro da janela. O motivo "compatibilidade" precisa ser investigado — o Bedrock suporta desde 2024.
**Trade-off**: validar suporte no modelo escolhido no startup; se indisponível, degradar (sem cache) sem quebrar.

## Invariantes
- Modelo por papel vem do **config** (specs 19/22), nunca hardcoded.
- Budget de sessão é **hard cap** (corta, não adverte).
- Caching indisponível → degradar sem quebrar.

## Dependências externas
| Serviço | Uso |
|---------|-----|
| Bedrock | Haiku (classifier) + Sonnet (agente/síntese) + prompt caching |

## Verificação
```bash
docker run --rm -v "$PWD:/app" -w /app python:3.11-slim sh -c \
  "pip install -q -r requirements.txt -r requirements-dev.txt && pytest tests/ -v --cov=src --cov-fail-under=90"
```
Testes (Bedrock mockado): classifier usa Haiku / agente usa Sonnet; `cache_control` presente no body; budget excedido corta; histórico truncado por tokens.

## Riscos
- Haiku degrada roteamento → fallback (06) + fan-out (17) cobrem; medir acurácia.
- Caching "incompatível" (motivo do comentário original) → validar no startup, degradar se preciso.
