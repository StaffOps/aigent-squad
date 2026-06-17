# Handoff — sessão 2026-06-16

Estado para retomar amanhã. O que foi feito, o que ficou pendente, e próximos
passos priorizados.

---

## Feito nesta sessão (commitado em `dev`, repo privado `staffops-aigent-squad`)

- **Migração**: código movido de `AIgent-squad` (público, congelado) → repo
  privado `staffops-aigent-squad` com histórico completo. Diretório local antigo
  removido. Repo de config renomeado para `staffops-aigent-config` (privado).
- **Terraform**: módulos `iam/` (IRSA + policies read-only), `dynamodb/`,
  `bedrock/` (VPC endpoints), `bedrock-aip/` (cost attribution). Tags centralizadas
  em `provider.default_tags`. Tudo validado.
- **Fix Bedrock**: `BEDROCK_MODEL_ID` precisa do inference profile `us.` (Sonnet 4.5
  não tem on-demand). Validado end-to-end (Bedrock respondeu).
- **MCP adapter** (`type: mcp`): agentes como MCP clients, allowlist read-only,
  fail-closed. Validado contra o `devops-mcp-kube` real do cluster.
- **Skills** (spec 26): conhecimento markdown lazy-loaded. 100% cov.
- **Cost attribution** (spec 27): AIP por modelo + métricas com `agent_id`.
- **Spec 14 (security)**: defense-in-depth anti-injection — **escrita, NÃO impl**.
- **Steering eficiência/custo**: novo pilar (`efficiency-cost.md`).
- **Competitive analysis**: `docs/COMPETITIVE-ANALYSIS.md` — 7 projetos + deep dive
  HolmesGPT/Aurora/OpenSRE + padrões para adotar.
- **Read-only reframe**: de "invariante eterno" → "postura atual; execução é
  futuro em aberto com guardrails + HITL".

---

## Pendências técnicas (bloqueantes / atenção)

1. **CI `test` job falha** no repo privado — `pip install` não clona
   `staffops-otel-libs` (git+ssh) por falta do secret `OTEL_LIBS_DEPLOY_KEY`.
   **Ação (sua, envolve credencial)**: adicionar deploy key do otel-libs como
   secret no repo. O workflow já está correto (usa `ssh-key`). Lint já passa.
2. **PR #1** (`dev`→`main`) fica `UNSTABLE` até o item #1 ser resolvido.
3. **Aurora/OpenSRE**: deep dive foi só dos 3 principais; esses 2 ainda em
   nível README se quiser aprofundar (Aurora=guardrails, OpenSRE=benchmark).

---

## Métricas — avaliação e próximos passos

**Estado**: 25 métricas, boa cobertura RED + domínio. Lacunas no eixo de
**eficiência** (o pilar definido hoje). Ações priorizadas:

### Reforçar labels (baixo esforço, alto valor)
- [ ] `aigent.tokens.total` / `aigent.cost.estimated`: adicionar label `model`
      (o `agent_id` já entrou na spec 27) — destrava rateio por modelo + ver tiering.

### Métricas novas focadas em eficiência (~4)
- [ ] `aigent.prompt.size_tokens` (histograma, por agente) — maior dreno de custo;
      detecta prompts inchando (ex: MCP/adapter trazendo dump grande).
- [ ] `aigent.investigation.rounds` (histograma) — distribuição real de rodadas
      vs o cap (regra de custo da steering).
- [ ] `aigent.llm.duration` separado de `aigent.collect.duration` — hoje
      `request.duration` mistura Bedrock + coleta; não dá pra achar o gargalo.
- [ ] `aigent.cache.tokens_saved` (counter) — quanto o cache de infra poupou.

### Organização (doc)
- [ ] Atualizar `docs/METRICS.md`: catalogar por **propósito** (RED / eficiência /
      qualidade), não por spec. Documentar **regra de cardinalidade**: `agent_id`,
      `model`, `direction`, `error_type` OK como label; `user_id`/`session_id`/
      `trace_id` NUNCA (vão pra traces/logs — ver steering observability).
- [ ] Painel Grafana: custo por agente, custo por modelo, prompt size p99,
      rodadas por investigação, cache savings.

---

## Candidatos a spec (do competitive analysis)

Priorizados por impacto:

1. **`28-rca-benchmark`** (de OpenSRE CloudOpsBench) — **maior gap**: não sabemos
   medir se a RCA está correta. Framework scored (cenários + custo-cap +
   anti-overfit + provenance). Resolve "qualidade", que nenhum guardrail resolve.
2. **Implementar spec 14 Phase 1** (Bedrock Guardrail + fail-closed) — segurança
   sai do papel. Referência de impl: Aurora `server/guardrails/` (NeMo + Sigma).
3. **Context budgeting universal** nos adapters (de HolmesGPT) — trunca/sumariza
   output antes do prompt. Ataca o maior dreno de tokens. Alimenta steering de
   eficiência. Pode virar parte de uma spec de adapters ou da 27.
4. **`28-llm-provider-abstraction`** (design only) — camada multi-provider
   (litellm candidato). Reabre ADR-001. Preservar cost-attribution (spec 27) é
   o ponto crítico. Implementar só com decisão explícita.

---

## O que pode ter passado (revisar amanhã com cabeça fresca)

- **Métricas de eficiência** acima — a steering de custo foi escrita HOJE mas as
  métricas que a tornam observável ainda não existem. Gap entre regra e medição.
- **Spec 14 é só design** — fácil esquecer que segurança "está pronta" quando só
  o plano está. Implementação é trabalho real.
- **`staffops-agent-config`**: os agentes convertidos têm `datasources: []` e
  keywords derivadas — precisam de tuning real antes de validar de verdade.
- **HolmesGPT `litellm`**: vale uma decisão consciente (reavaliar ADR-001?) —
  não deixar virar dívida silenciosa.
- **Deploy real**: nada foi aplicado na AWS nem deployado. Todo o Terraform é
  `validate`-only. O salto "spec/código → rodando em prod" é o maior trabalho
  ainda não começado.
