# Design: Security Hardening — Anti-Prompt-Injection Defense-in-Depth

## Arquitetura (defense-in-depth)

Cada requisição atravessa camadas independentes. Uma camada comprometida não
compromete as outras (princípio: nenhuma camada confia na anterior).

```
                 untrusted input (query / adapter / mcp / history / skill)
                              │
                 ┌────────────▼─────────────┐
   Layer 2       │ Input pre-scan            │  normalize (unicode/base64/
   (pre-LLM)     │ + heuristics              │  homoglyph) → cheap reject
                 └────────────┬─────────────┘
                 ┌────────────▼─────────────┐
   Layer 3       │ Context isolation         │  <untrusted> blocks +
                 │ (prompt construction)     │  system reinforcement
                 └────────────┬─────────────┘
                 ┌────────────▼─────────────┐
   Layer 1       │ Bedrock Guardrail (INPUT) │  prompt-attack / denied topics /
   (primary)     │                           │  PII — model-based, multi-language
                 └────────────┬─────────────┘
                 ┌────────────▼─────────────┐
   Layer 5       │ Canary injection          │  tokens embedded in infra_data
                 └────────────┬─────────────┘
                       Bedrock InvokeModel
                 ┌────────────▼─────────────┐
   Layer 1       │ Bedrock Guardrail (OUTPUT)│  grounding / PII / denied content
                 └────────────┬─────────────┘
                 ┌────────────▼─────────────┐
   Layer 4       │ Output filter             │  PII/secret/canary leak scan
                 └────────────┬─────────────┘
                              ▼ response (or 403 fail-closed)

   Layer 6 (cross-cutting): rate limit + budget cap per user/session
   Audit log (cross-cutting): every detection/refusal
```

## STRIDE threat model

| STRIDE | Ameaça | Mitigação nesta spec |
|--------|--------|----------------------|
| **S**poofing | Requisição se passa por usuário/serviço legítimo | Auth `X-Internal-Token` (spec 04) + rate limit por identidade (L6) |
| **T**ampering | Injection altera comportamento do agente | Guardrail (L1) + pre-scan (L2) + context isolation (L3) |
| **R**epudiation | Ataque sem rastro | Audit log estruturado de toda detecção/recusa |
| **I**nfo disclosure | Exfiltração de infra/PII via resposta | Output filter (L4) + canary (L5) + Guardrail output (L1) |
| **D**oS / abuso | Token burn, loops caros | Rate limit + budget cap (L6) + max rounds (existente) |
| **E**levation | Agente é levado a "agir" | **Read-only (fase atual)** (4 camadas) — bloqueia na raiz HOJE; se execução for habilitada, vira a ameaça dominante e exige human-in-the-loop |

> O "E" (elevation) — a ameaça mais grave nos concorrentes que agem — é
> neutralizado pela arquitetura read-only, não por esta spec. Esta spec foca
> em T/I/D, que é onde o read-only NÃO ajuda.

## Componentes (camadas)

| # | Componente | Onde | Responsabilidade |
|---|-----------|------|------------------|
| L1 | `GuardrailClient` (Bedrock) | wrapper no `bedrock.invoke` | Avaliação input+output independente do modelo |
| L2 | `InputScanner` | antes de montar contexto | Normalização + heurísticas baratas |
| L3 | Context isolation | `generic_agent` (existe, reforçar) | Delimitação `<untrusted>` + system reinforcement |
| L4 | `OutputFilter` | após invoke | PII/segredo/canary na resposta |
| L5 | `CanaryGuard` | injeção no `infra_data` + checagem na saída | Detecção de exfiltração |
| L6 | `RateLimiter`/`BudgetGuard` | supervisor entrypoint | Anti-abuso (Redis) |

## Rationale (decisões)

### Decisão 1: Bedrock Guardrails como camada primária (não regex próprio)

**Escolha**: usar AWS Bedrock Guardrails como detector primário de
prompt-attack, em vez de construir detecção própria.

**Justificativa, em ordem de força**:
1. **Independência do modelo**: o Guardrail avalia separadamente do invoke do
   agente. Um injection que engana o LLM **não** engana o Guardrail (avaliações
   distintas). Isso é defense-in-depth de verdade, não a mesma camada duas vezes.
2. **Multi-idioma nativo**: detecção de prompt-attack do Bedrock cobre múltiplos
   idiomas — atende ao requisito "qualquer idioma" sem nós treinarmos nada.
   Diferencial direto: o Azure SRE Agent **só suporta inglês**.
3. **Gerenciado + evolutivo**: AWS atualiza os detectores; não viramos donos de
   um classificador de jailbreak (que envelhece rápido).
4. Já estamos no Bedrock (mesmo plano de IAM/rede) — custo de integração baixo.

**Trade-offs aceitos**:
| Custo | Realidade |
|-------|-----------|
| Custo por avaliação (input+output) por request | Segurança é o produto — usuário aceitou |
| Latência extra por request | Aceito; mitigável com pre-scan barrando lixo antes (L2) |
| Dependência de serviço AWS | Alinhado à stack; fail-closed cobre indisponibilidade |

**Quando estaria errada**: se o Guardrail tiver taxa de falso-positivo alta a
ponto de inviabilizar uso legítimo, ou se um requisito on-prem/multi-cloud
proibir dependência AWS. Aí: detector próprio ou OSS (ex: Llama Guard) como L1.

**Alternativas descartadas**:
- Regex/heurística como camada primária — frágil, contornável, não multi-idioma
  (rebaixado para L2, barato, complementar).
- Llama Guard self-hosted — mais operação, sem ganho claro vs. gerenciado agora.

### Decisão 2: Fail-closed (segurança > disponibilidade)

**Escolha**: se o Guardrail/serviço de segurança não responde, **recusar** a
query (403), não fazer bypass.

**Justificativa**:
1. O produto inteiro se vende como "seguro e confiável". Um bypass sob falha
   destrói a garantia — pior que ficar indisponível.
2. Read-only já limita o dano de um bypass, mas exfiltração/manipulação ainda
   ocorreriam. Não vale o risco.
3. **Contraste competitivo**: os concorrentes priorizam disponibilidade (um SRE
   tool que cai durante incidente é inútil). Nós aceitamos a tensão e
   escolhemos segurança — porque NÃO somos o caminho crítico de remediação
   (somos consultivos); se cairmos, o operador ainda tem suas ferramentas.

**Trade-offs aceitos**:
| Custo | Realidade |
|-------|-----------|
| Indisponibilidade sob falha do guardrail | Aceito; somos consultivos, não o executor |
| Tensão com "resiliência" pedida | Resolvida a favor de segurança NESTE ponto; resiliência (circuit breaker, fail-open) permanece para dependências NÃO-críticas de segurança (DynamoDB, cache) |

**Quando estaria errada**: se o produto evoluir para caminho crítico de
operação (improvável dado o read-only).

### Decisão 3: read-only NÃO é desta spec — é pré-requisito da fase atual

**Escolha**: esta spec **não** adiciona enforcement de read-only; ela depende
do read-only existente (4 camadas) como dado.

**Justificativa**: read-only é o que torna o threat model tratável (elimina
elevation/mutação). Misturar as duas diluiria ambas. Esta spec assume read-only
e ataca o que sobra (exfiltração/manipulação/custo).

**Signal de reabertura**: se algum dia o read-only for relaxado (agente passa a
agir), esta spec precisa ser **reescrita** — o threat model muda completamente
(elevation volta a ser a ameaça dominante, human-in-the-loop vira obrigatório).

## Posicionamento competitivo (justifica o design)

| Dimensão | Datadog Bits / incident.io / PagerDuty / Azure SRE | AIgent-squad |
|----------|---------------------------------------------------|--------------|
| Autonomia | Agem (rollback/scale/restart) | **Read-only hoje** (execução é futuro em aberto, com guardrails) |
| Mitiga injection→mutação via | Human-in-the-loop (muleta) | **Arquitetura** hoje (sem code path de mutação); HITL obrigatório se/quando executar |
| Blast radius de injection | Alto (pode executar) | **Baixo hoje** (só leitura) |
| Multi-idioma anti-injection | Limitado (Azure: só inglês) | **Sim** (Bedrock Guardrails) |
| Postura sob falha de segurança | Disponibilidade-first | **Fail-closed** (segurança-first) |
| Pitch | "automatiza remediação" | **"read-only por padrão; quando agir, com guardrails que os outros não têm desde o início"** |

> Hoje não competimos em autonomia — competimos em **confiança verificável**.
> Quando a execução entrar, esta spec é o que permite agir *com* a garantia que
> os concorrentes só adicionaram depois. É a materialização técnica do pitch.

## Invariantes

- Nenhuma camada confia na anterior (defense-in-depth real).
- Fail-closed em qualquer falha de componente de segurança (L1, L2, L4).
- Audit log nunca registra o payload malicioso em claro (evita log injection /
  re-exposição).
- `agent_id`/`user_id`/`session_id` em todo evento de auditoria (rastreio).
- Read-only é a postura da fase atual (esta spec não o toca, nem o torna eterno).

## Dependências externas

| Serviço | Propósito |
|---------|-----------|
| AWS Bedrock Guardrails | Detecção primária input/output (L1) |
| Redis | Rate limit + budget counters (L6) |
| OTel/audit sink | Log estruturado de detecções |

## Fases (não-big-bang)

A implementação é incremental (ver tasks.md). Ordem por valor/risco:
L1 (Guardrail) + fail-closed primeiro (maior ganho), depois L4/L5 (exfil),
depois L2 (otimização de custo), L6 (abuso), por fim a suíte multi-idioma como
gate de regressão.
