# Efficiency & Cost — Project Principle

Eficiência é um **pilar de primeira classe** do AIgent-squad, no mesmo nível de
segurança e correção. Não significa só velocidade/latência — significa
**custo por resultado**: tokens, chamadas de LLM, e recursos gastos para
entregar uma RCA/resposta útil.

> Regra de ouro: **o resultado mais barato que ainda é correto e seguro vence.**
> Latência baixa que queima tokens à toa NÃO é eficiente.

---

## Por que isto importa (não é opcional)

O Bedrock cobra **por token** (input + output, output ~5x mais caro). Cada
token de contexto desnecessário, cada rodada de LLM evitável, cada retry cego é
**dinheiro**. Num sistema multi-agente com fan-out + RCA multi-round, o custo
escala rápido. Eficiência de custo é requisito de produto, não afinação tardia.

## CRITICAL: pensar em custo ANTES de implementar

Antes de adicionar qualquer caminho que invoque LLM ou monte contexto, perguntar:
1. **Esse contexto todo precisa ir pro prompt?** (input tokens = custo recorrente)
2. **Dá pra resolver com modelo mais barato?** (classifier/triagem ≠ síntese)
3. **Dá pra cachear / curto-circuitar antes do LLM?**
4. **Quantas rodadas de LLM isso dispara no pior caso?** (cap obrigatório)

---

## Práticas obrigatórias

### Contexto (input tokens)
- **Truncar/sumarizar output de tool/adapter/MCP antes do prompt.** Dados de
  infra grandes (ex: listas de eventos, logs) explodem input tokens. Padrão
  HolmesGPT: server-side filtering + spill-to-disk + transformer de resumo com
  modelo rápido. O `McpAdapter` já trunca por tool (4000 chars) — estender essa
  disciplina a todos os adapters.
- **Lazy injection.** Skills só entram no prompt quando a query casa (já feito —
  spec 26). Mesma regra para qualquer conhecimento/contexto opcional.
- **Histórico limitado.** Injetar só as N mensagens relevantes, não a sessão toda.

### Modelo (tiering)
- **Modelo barato para tarefas baratas.** Classifier/triagem/roteamento devem
  usar um modelo rápido/barato (ex: Haiku); síntese/RCA usam o caro (Sonnet/
  Opus). Não usar o modelo premium para decidir roteamento. (Ver spec 11.)
- **Não sobre-dimensionar `max_tokens`** de saída — limita custo do output.

### Rodadas e retries
- **Cap de rodadas obrigatório** em todo fluxo multi-round (RCA, fan-out). Sem
  cap = custo ilimitado sob falha. (Ver limites por nível no ROADMAP.)
- **Retry com backoff, não cego.** Retry que reinvoca o LLM multiplica custo —
  só em erros transitórios, com teto.
- **Anti-loop**: barrar tool-call repetida idêntica (padrão HolmesGPT
  `prevent_overly_repeated_tool_call`).

### Cache
- **Cachear dados de infra** (determinístico, TTL) — não a resposta do LLM
  (vaza entre usuários, quebra multiturno — ver project.md).
- Avaliar **prompt caching do Bedrock** para system prompt + skills repetidos
  (desconto grande em tokens cacheados). (Ver spec 11.)

### Medição (não dá pra otimizar o que não se mede)
- **Custo/tokens por agente** já instrumentado (`aigent.tokens.total`,
  `aigent.cost.estimated` labelados por `agent_id` — spec 27). Toda feature que
  muda padrão de uso de LLM deve observar o impacto nessas métricas.
- **Budget cap por usuário/sessão** (spec 14) protege contra abuso E custo.

---

## Trade-off com as outras dimensões

Eficiência **não** atropela segurança nem correção:
- Segurança fail-closed (spec 14) tem custo de latência/avaliação — **aceito**.
- Uma RCA correta com 1 rodada extra > uma RCA barata e errada.
- A ordem de prioridade: **correto > seguro > eficiente > rápido**. Eficiência
  vem antes de velocidade pura, mas depois de correção e segurança.

---

## Anti-patterns

- ❌ Injetar dump bruto de adapter/MCP no prompt sem truncar/sumarizar
- ❌ Usar modelo premium para classificação/roteamento
- ❌ Fluxo multi-round sem cap de rodadas
- ❌ Retry cego que reinvoca LLM sem teto
- ❌ Cachear resposta de LLM por query (vaza + incorreto)
- ❌ Otimizar latência queimando tokens (não é eficiência)
- ❌ Adicionar feature que muda uso de LLM sem olhar `aigent.cost.estimated`
