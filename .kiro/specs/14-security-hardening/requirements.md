# Feature: Security Hardening — Anti-Prompt-Injection Defense-in-Depth

**Spec**: `14-security-hardening`
**Severidade**: 🔴 Critical (posicionamento de produto, não só feature)
**Depende de**: `04-harden-security` (auth, non-root, delimitação básica — done)
**Relacionado**: `ADR-001` (read-only), `docs/READ_ONLY_POLICY.md`, ROADMAP spec 14

---

## Tese (por que isto é o produto, não um detalhe)

O AIgent-squad compete na mesma categoria que Datadog Bits AI SRE, incident.io,
PagerDuty AI SRE e Azure SRE Agent. **A diferença deliberada (hoje)**: esses
produtos **agem** (rollback, scale, restart — com human-in-the-loop como muleta
de segurança). O AIgent-squad é **read-only na fase atual** — hoje não executa.

> **Read-only não é permanente.** Executar ações é uma possibilidade futura em
> aberto no roadmap (não está descartada). Mas a postura de segurança DEPENDE
> de qual modo está ativo, e esta spec é **pré-requisito bloqueante** para
> habilitar execução:
> - **Enquanto read-only** (hoje): o pior caso de um injection é exfiltração/
>   manipulação/custo — nunca mutação. Esta spec endurece essa superfície.
> - **Se/quando executar** (futuro): o "Elevation" do STRIDE volta a ser a
>   ameaça dominante; injection poderia disparar ação destrutiva. Então
>   execução é condicionada a (a) esta spec implementada E (b)
>   **human-in-the-loop obrigatório** para qualquer ação mutante.

Isso inverte a relação com prompt injection:
- Num agente que **age**, um injection bem-sucedido = incidente de produção
  (ex: "role back the prod database" executado).
- Num agente **read-only**, o pior caso de um injection = exfiltração de dado
  coletado, manipulação da recomendação, ou abuso de custo. **Nunca mutação.**

Portanto o posicionamento atual é: **"o AI SRE em que você confia — read-only
por padrão, e quando agir, agirá com guardrails que os outros não têm desde o
início"**. Guardrails fortíssimos + anti-injection multi-idioma **são o
diferencial competitivo** (e o que torna a execução futura segura), não um
nice-to-have. Esta spec eleva a defesa de injection de 1 camada (delimitação
textual) para defense-in-depth real.

## Threat model (resumo — STRIDE completo no design)

Na fase read-only atual, a classe "mutação" está eliminada, então o foco é
exfiltração/manipulação/custo. **Quando execução for habilitada, este threat
model precisa ser estendido** (elevation/mutação voltam — ver design.md):

| Ameaça | Vetor | Impacto |
|--------|-------|---------|
| **Exfiltração** | Injection faz o agente vazar inventário/configs coletados | Vazamento de dados de infra |
| **Manipulação de resposta** | Injection faz o agente recomendar algo malicioso ao operador | Operador age errado com base em conselho envenenado |
| **Abuso de custo** | Injection força invocações caras / loops | Token burn, custo |
| **Jailbreak multi-idioma** | Ataque em PT/ES/zh/etc. ou ofuscado (base64, leetspeak, unicode) | Burla delimitação textual |
| **Cross-tenant leak** | Dados de uma sessão/usuário vazam pra outra | Quebra isolamento |

## User Stories

WHEN qualquer input não-confiável (query do usuário, saída de adapter/MCP,
histórico, skill) entra no fluxo THEN o sistema SHALL avaliá-lo contra um
guardrail independente do prompt do agente, **em qualquer idioma**.

WHEN o guardrail detecta prompt attack/jailbreak THEN o sistema SHALL recusar
a requisição (fail-closed) e registrar o evento — não tentar "limpar" e seguir.

WHEN o guardrail/serviço de segurança está indisponível THEN o sistema SHALL
**recusar** (fail-closed), priorizando segurança sobre disponibilidade.

WHEN a resposta do agente é gerada THEN ela SHALL passar por filtro de saída
(PII, segredos, canary tokens) ANTES de retornar ao usuário.

WHEN dados de infra são injetados no contexto THEN eles SHALL conter canary
tokens que, se aparecerem na saída, sinalizam exfiltração.

WHEN o sistema processa requisições THEN ele SHALL impor rate limit + budget
cap por usuário/sessão para conter abuso de custo via injection.

## Acceptance Criteria

- [ ] **Camada 1 — Bedrock Guardrails** ativo em todo invoke (input + output),
      com prompt-attack detection, denied topics, PII, multi-idioma.
- [ ] **Camada 2 — Input pre-scan**: normalização (unicode/base64/homoglyph) +
      heurísticas antes do LLM; detecções óbvias barram sem custo de invoke.
- [ ] **Camada 3 — Context isolation**: dados não-confiáveis em blocos
      delimitados + instrução de sistema reforçada (mantém o que já existe).
- [ ] **Camada 4 — Output filter**: PII/segredos/canary scan na resposta.
- [ ] **Camada 5 — Canary tokens**: injetados no contexto de infra; leak →
      alerta + bloqueio.
- [ ] **Camada 6 — Rate limit + budget cap** por usuário/sessão (anti-abuso).
- [ ] **Fail-closed**: indisponibilidade do guardrail → 403, não bypass.
- [ ] **Multi-idioma comprovado**: suíte de testes com ataques em ≥5 idiomas +
      ofuscações (base64, leetspeak, zero-width, unicode confusables).
- [ ] **Audit log** estruturado de toda detecção/recusa (sem vazar o payload
      malicioso em claro nos logs).
- [ ] **Read-only mantido na fase atual** (não regredir acidentalmente); habilitar execução é decisão explícita e fora desta spec.
- [ ] Cobertura de testes ≥90% no código novo.

## Fora de escopo

- Habilitar o agente a agir/executar — **trabalho futuro em aberto**, fora
  desta spec. Quando endereçado, exige estender o threat model (elevation) +
  human-in-the-loop + esta spec implementada como pré-requisito.
- WAF de rede / DDoS (camada de infra, não de aplicação — outra spec).
- Treinar modelo próprio de detecção (usa Bedrock Guardrails gerenciado).

## Trade-offs declarados (decididos pelo usuário)

- **Custo**: cada invoke ganha custo de avaliação do Guardrail (input+output).
  Aceito — segurança é o produto.
- **Latência**: +avaliação por request. Aceito.
- **Disponibilidade**: fail-closed reduz disponibilidade sob falha do guardrail.
  Aceito — segurança > uptime para este produto (diferente dos concorrentes,
  que priorizam disponibilidade).
