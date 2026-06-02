# Design: Agent Capability Manifest

## Arquitetura

Um registry capability-rich, alimentado por manifestos YAML auto-descobertos. Estende o `AgentRegistry` do `staffops-chaitops` (que já faz discovery + `required_env` + `/ready`) adicionando os campos de **colaboração** e **evidência**.

```
config/agents/*.yaml ─▶ AgentRegistry (startup discovery + validação)
                              │
        ┌─────────────────────┼──────────────────────────┐
        ▼                     ▼                          ▼
   Classifier            Coordinator (spec 17)      RCA flow (spec 18)
   usa name/description/  seleciona N agentes por    usa evidence_types
   capabilities/keywords  capability/domain +        p/ montar coleta
                          resolve delegates_to
```

A lista de agentes **deixa de existir no código** — vive só nos manifestos.

## Schema do manifesto

```yaml
# config/agents/observability.yaml
name: observability
description: >
  Consulta métricas, logs e traces (VictoriaMetrics, Loki, Tempo).
  Responde sobre error rate, latência, saúde de serviço e anomalias.
domain: observability
capabilities: [metrics_query, log_query, trace_query, anomaly_lookup]
routing_keywords: [latency, error rate, prometheus, grafana, logs, trace, p99, slo]
datasources: [prometheus, loki, tempo]          # o que ele acessa de fato
evidence_types: [metric, log, trace]            # o que contribui numa RCA (spec 18)
delegates_to:                                   # COMO se ajuda — dirigido por dados
  - agent: kubernetes
    when: "metric anomaly points to pod restarts / OOM / scheduling"
  - agent: devops
    when: "latency regression correlates with a recent deploy"
read_only: true
model_tier: standard                            # fast | standard | premium
required_env: []
sidecar_url: http://observability-agent:8005/process
enabled: true
```

Campos e papéis:

| Campo | Quem consome | Para quê |
|-------|-------------|----------|
| `name`, `description` | classifier | roteamento semântico (o que você pediu) |
| `domain`, `capabilities` | coordinator | seleção multi-agente (match de N) |
| `routing_keywords` | classifier | desempate / fast-path sem LLM |
| `datasources` | RCA, ops | saber o que o agente acessa de verdade |
| `evidence_types` | RCA (spec 18) | montar a coleta de evidência por tipo de sinal |
| `delegates_to` (`agent`+`when`) | coordinator, agent-as-tools | **como os agentes se ajudam** — sua peça-chave |
| `read_only` | safety | invariante; nunca rotear mutação |
| `model_tier` | bedrock layer | custo por papel (specs 11/19) |
| `required_env`, `sidecar_url`, `enabled` | registry | discovery/health (herdado do chaitops) |

## Rationale (decisões e trade-offs)

### Decisão 1: Colaboração declarativa via `delegates_to` (não matriz hardcoded, não LLM puro)

**Escolha**: cada agente declara **no próprio manifesto** a quem delega e **sob que condição** (`when`), em vez de (a) uma matriz de colaboração no código ou (b) o LLM adivinhar do zero toda vez.

**Justificativa, em ordem de força**:
1. **Roster aberto exige isso**: se a colaboração fosse hardcoded, adicionar o 12º agente exigiria editar código de todos os outros. Declarativo = adicionar manifesto e pronto.
2. **Dá ao LLM um prior barato e auditável**: o `when` textual guia o coordinator/classifier sem uma chamada extra cara só pra descobrir "quem ajuda quem". O LLM ainda decide, mas com dica.
3. **Versionável e revisável**: colaboração vira diff de YAML num PR, não lógica enterrada.

**Trade-offs aceitos**:
| Custo | Realidade |
|-------|-----------|
| Manter `delegates_to` à mão | É 2-4 linhas por agente; muito mais barato que matriz N×N no código |
| Pode ficar stale | Validação no startup pega destino órfão; revisão em PR pega o resto |

**Quando estaria errada** (signals pra reabrir): se o roster crescer a ponto de `delegates_to` virar inadministrável manualmente → aí sim avaliar inferência por LLM/embedding (fora de escopo agora).

**Alternativas descartadas**:
- **Matriz no código** — não escala com roster aberto (o ponto do usuário).
- **LLM infere tudo sempre** — custo por query + não-determinístico + não-auditável.

### Decisão 2: Estender o `AgentRegistry` do chaitops, não criar outro

**Escolha**: adicionar campos ao manifesto/registry existente do chaitops em vez de um registry novo no AIgent-squad.

**Justificativa**: o chaitops já tem discovery + `required_env` + `/ready` testados (ver `ECOSYSTEM.md`). Reusar evita a duplicação de plataforma que o `ECOSYSTEM.md` alerta. Os campos novos (`capabilities`, `evidence_types`, `delegates_to`) são aditivos e retrocompatíveis.

**Trade-off aceito**: acopla o AIgent-squad ao schema do chaitops — aceitável e desejável dado o reposicionamento recomendado (Opção B).

## Invariantes

- Lista de agentes **nunca** hardcoded — só manifestos.
- `delegates_to.agent` **sempre** existe no roster (validado no startup).
- `read_only: true` é honrado em todo caminho de roteamento.
- Adicionar agente = adicionar manifesto (zero código).
- Manifesto inválido = falha no **startup**, nunca no request.

## Dependências externas

| Lib/serviço | Uso |
|-------------|-----|
| `pydantic` + `PyYAML` | schema + parse |
| `AgentRegistry` (chaitops) | base de discovery a estender |

## Verificação

```bash
docker run --rm -v $(pwd):/app -w /app python:3.11-slim sh -c \
  "pip install -q -r requirements.txt pytest && pytest tests/ -v --cov=src --cov-fail-under=90"
```

Testes-chave (test-author ≠ autor): discovery de N manifestos; classifier seleciona por `capabilities`/`keywords`; coordinator seleciona conjunto multi-agente; `delegates_to` órfão → erro de startup; ciclo direto A→B→A detectado; `read_only` nunca recebe mutação; adicionar manifesto novo aparece no roster sem código.

## Riscos

- `delegates_to` stale → mitigado por validação de startup + revisão em PR.
- Explosão de agentes com capabilities sobrepostas → desempate por `routing_keywords`/`domain`/`priority`; documentar convenção de nomes.
- Acoplamento ao schema do chaitops → aceitável dado o reposicionamento; manter campos aditivos.
