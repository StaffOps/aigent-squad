# Design: Multi-Agent Collaboration

## Arquitetura

Mantém hub-and-spoke (supervisor coordena), mas o hub passa a fazer **fan-out/fan-in** quando a query é cross-domain:

```
                         ┌─ aws-agent ──────┐
User → Supervisor → Classifier (1..N) ─┼─ finops-agent ───┤→ Synthesizer → resposta única
                         └─ observability ──┘   (1 Bedrock call)
                         (asyncio.gather, paralelo)
```

- **N=1** (caso comum): fast-path — chama 1 agente e devolve direto. **Zero** custo de síntese.
- **N≥2**: chama os agentes em paralelo, coleta as respostas, sintetiza.
- **Agent-as-tools**: ortogonal ao fan-out — um agente, ao processar, pode requisitar dado a outro (1 salto), reusando o mesmo endpoint `/process` com um header de profundidade.

## Componentes

| Componente | Responsabilidade | Onde |
|-----------|------------------|------|
| Classifier (multi) | Retornar lista ordenada de agentes relevantes | `src/core/classifier.py` (estende contrato) |
| Orchestrator (fan-out) | `asyncio.gather` dos N agentes + tolerância a falha parcial | `src/supervisor/agent.py` |
| Synthesizer | 1 chamada Bedrock que funde N respostas → 1, com atribuição | `src/supervisor/synthesizer.py` (novo) |
| Hop guard | Header `X-Agent-Hop` limita agent-as-tools a depth=1 | `src/core/agent_base.py` + servers |
| Agent tool-call | Cliente fino p/ um agente chamar outro via supervisor | `src/core/agent_tools.py` (novo) |

## Contrato do classifier (retrocompatível)

```python
@dataclass
class ClassifierResult:
    agents: list[AgentMatch]        # NOVO — ordenado por relevância
    reasoning: Optional[str] = None
    @property
    def selected_agent(self) -> str:  # compat: primeiro da lista ou "unknown"
        return self.agents[0].agent if self.agents else "unknown"

@dataclass
class AgentMatch:
    agent: str
    confidence: float
```

O system prompt do classifier passa a permitir 1..N agentes e a explicar quando usar mais de um (query multi-faceta) vs um só (follow-up, domínio único). `max_agents` (default 3) trunca a lista.

## Fan-out (fan-in)

```python
# supervisor, quando len(agents) >= 2
async def _fan_out(self, agents, payload):
    tasks = [self._call_agent(a.agent, payload) for a in agents]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    ok = [(a.agent, r) for a, r in zip(agents, results) if not isinstance(r, Exception)]
    failed = [a.agent for a, r in zip(agents, results) if isinstance(r, Exception)]
    return ok, failed   # falha parcial não derruba a query
```

Tempo total ≈ `max()` das latências, não a soma. Reusa o circuit breaker e os timeouts da spec 06.

## Síntese

Uma única chamada Bedrock recebe: a query original + as N respostas rotuladas por agente, e produz a resposta final preservando atribuição. Usa modelo **Sonnet** (qualidade da fusão importa), enquanto o classifier usa **Haiku** (spec 11). Se `failed` não-vazio, o prompt instrui a notar a degradação.

## Agent-as-tools (depth = 1)

```
agent A (processando) ── precisa de dado de B ──▶ POST /process (X-Agent-Hop: 1)
agent B responde ──▶ A usa no seu contexto ──▶ resposta de A
```

- `X-Agent-Hop` ausente/0 = chamada do usuário; `=1` = chamada agente→agente; `≥2` = **rejeitada** (quebra ciclo).
- Read-only preservado: B é o mesmo agente consultivo de sempre.
- Exposto como ferramenta opcional; um agente só usa quando o prompt detecta necessidade cross-domain.

## Rationale (decisões e trade-offs)

### Decisão 1: Fan-out + síntese no supervisor (não malha agente-a-agente)

**Escolha**: a colaboração multi-domínio é orquestrada pelo supervisor (fan-out/fan-in), não por agentes conversando livremente entre si.

**Justificativa, em ordem de força**:
1. **Controle de custo e terminação**: malha livre agente↔agente não tem limite natural de saltos → explosão de chamadas Bedrock e risco de ciclo. Fan-out no hub tem teto determinístico (`max_agents`, 1 síntese).
2. **Observabilidade**: 1 trace em árvore (supervisor → N folhas → síntese) é legível; um grafo arbitrário de chamadas é quase impossível de depurar.
3. **Reuso**: o supervisor já tem cliente HTTP, circuit breaker (spec 06) e histórico — o fan-out reaproveita tudo.

**Trade-offs aceitos**:
| Custo | Realidade |
|-------|-----------|
| Não há "debate" entre agentes (1 rodada só) | Cobre 90% dos casos cross-domain; debate é over-engineering pra um ChatOps consultivo |
| Síntese adiciona 1 chamada Bedrock quando N≥2 | Só no caso multi-domínio; N=1 (maioria) não paga isso |

**Quando essa decisão estaria errada** (signals pra reabrir):
- Casos reais exigem múltiplas rodadas (A responde, B refina, A reconsidera) com frequência.
- O teto de `max_agents=3` se mostra insuficiente para queries reais.

**Alternativas descartadas**:
- **Malha P2P agente-a-agente** — descartada por custo/ciclo/observabilidade.
- **LangGraph/framework de orquestração** — descartado: foi removido do projeto deliberadamente (ver steering `project.md`); reintroduzir contraria decisão vigente.

### Decisão 2: Agent-as-tools limitado a 1 salto

**Escolha**: um agente pode chamar **no máximo um** outro agente, nunca encadeado.

**Justificativa**:
1. **Anti-ciclo**: depth=1 torna ciclos impossíveis por construção (A→B, B não pode chamar mais ninguém).
2. **Latência/custo previsíveis**: pior caso = 2 agentes + 1 síntese, não uma cadeia indefinida.
3. **Simplicidade de teste**: o espaço de estados é pequeno e enumerável.

**Trade-offs aceitos**:
| Custo | Realidade |
|-------|-----------|
| Cadeias A→B→C impossíveis | Se C é necessário, o fan-out do supervisor (Decisão 1) já o inclui em paralelo |

**Quando estaria errada**: se surgirem dependências legítimas de 2+ saltos que o fan-out não resolve.

### Decisão 3: Classifier multi-agente retrocompatível (lista, com `selected_agent` derivado)

**Escolha**: estender `ClassifierResult` para uma lista e expor `selected_agent` como propriedade (primeiro item).

**Justificativa**: não quebra o supervisor atual nem a spec 02/06 enquanto o fan-out é introduzido; o caminho N=1 continua idêntico. Migração incremental.

**Trade-off aceito**: um campo derivado a manter — custo trivial perto de um breaking change no contrato.

## Invariantes

- N=1 **nunca** dispara síntese (fast-path imutável).
- `X-Agent-Hop ≥ 2` é sempre rejeitado.
- Falha parcial no fan-out **degrada**, não derruba.
- Read-only preservado em todos os caminhos (fan-out e agent-as-tools).
- Teto `max_agents` aplicado **antes** de qualquer chamada (proteção de custo).

## Dependências externas

| Serviço | Uso |
|---------|-----|
| Bedrock | classifier (Haiku) + agentes + síntese (Sonnet) |
| (herda) | endpoints `/process` dos 5 agentes |

## Verificação

```bash
docker run --rm -v $(pwd):/app -w /app python:3.11-slim sh -c \
  "pip install -q -r requirements.txt pytest pytest-asyncio && pytest tests/ -v --cov=src --cov-fail-under=90"
```

Testes-chave (test-author ≠ autor do código): classifier devolve N agentes p/ query multi-domínio; `asyncio.gather` roda em paralelo (assert tempo ≈ max, com agentes mockados c/ sleep); síntese funde N→1; falha parcial inclui nota de degradação; `X-Agent-Hop=2` retorna rejeição; N=1 não chama o synthesizer.

## Riscos

- Custo: fan-out multiplica chamadas Bedrock. Mitigado por `max_agents` + Haiku no classifier + circuit breaker.
- Qualidade da síntese: prompt mal calibrado funde respostas de forma confusa. Mitigar com exemplos no prompt + atribuição explícita.
- Pré-requisito async (spec 06): sem ele, `asyncio.gather` não dá paralelismo real (boto3 síncrono bloqueia o loop).

---

## Extensibilidade: contexto compartilhado (Nível 3+ do ROADMAP)

> Esta seção documenta como o fan-out evolui sem reescrita. NÃO implementar no Nível 1–2.

**Problema do Nível 3**: agentes coletam independentemente; cada um não sabe o que os outros encontraram. Isso limita a qualidade quando a evidência de um agente MUDARIA a query de outro.

**Solução**: o `asyncio.gather` passa a aceitar um **scratchpad** (spec 18 `InvestigationState`) como contexto injetado no prompt dos agentes em rodadas subsequentes.

```python
# Nível 1-2: fan-out simples
results = await asyncio.gather(*[call_agent(a, payload) for a in agents])

# Nível 3+: fan-out COM contexto compartilhado
for round in range(max_rounds):
    context = scratchpad.summary()  # resumo das rodadas anteriores
    enriched_payload = {**payload, "prior_evidence": context}
    results = await asyncio.gather(*[call_agent(a, enriched_payload) for a in agents])
    scratchpad.update(results)
    if synthesizer.evidence_sufficient(scratchpad):
        break
```

**O que muda no contrato dos agentes**: recebem campo opcional `prior_evidence` (string, resumo). Agentes que não suportam (Nível 1) ignoram o campo. Zero breaking change.

**Promotion trigger**: "contexto de outros agentes melhoraria a coleta em >20% dos casos" (medido pela diff de confiança da RCA com/sem contexto em testes A/B).
