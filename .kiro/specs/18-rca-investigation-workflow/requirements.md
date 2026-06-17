# Feature: RCA Investigation Workflow

**Spec**: `18-rca-investigation-workflow`
**Severidade**: 🟢 Feature (o diferencial do produto)
**Origem**: ganho esperado do usuário (troubleshooting + RCA); steering `investigation-protocol.md`, skill `root-cause-analysis`
**Depende de**: `17-multi-agent-collaboration` (fan-out de evidência), `09-otel-instrumentation` (trace), `19-config-driven-platform` (datasources via config)

Transforma o sistema de "responde 1 pergunta" em "**investiga um problema**". Dado um sintoma (alerta ou relato), o supervisor coordena os agentes em paralelo pra coletar evidência factual, constrói uma **timeline**, **correlaciona sinais** e produz uma **RCA com nível de confiança, evidência e prevenção** — em vez de uma resposta de um único agente.

## User Stories

WHEN o usuário descreve um sintoma ("latência subiu às 14h no serviço X") THEN o sistema SHALL iniciar uma investigação, não apenas rotear pra 1 agente.

WHEN uma investigação inicia THEN o supervisor SHALL fazer fan-out **paralelo** de coleta de evidência: observability (métricas/logs/traces), kubernetes (events/restarts/OOM), devops (deploys/MRs recentes no período), aws (saúde de infra).

WHEN a evidência é coletada THEN o sistema SHALL construir uma **timeline** com timestamps (causa precede efeito) e correlacionar os sinais.

WHEN ≥3 sinais independentes apontam a mesma causa THEN o sistema SHALL declarar uma RCA com confiança "alta"; com menos, "média/baixa" + o que falta confirmar.

WHEN uma RCA é declarada THEN ela SHALL incluir: hipótese, evidência (com hierarquia forte/média/fraca), timeline, e **proposta de prevenção** (alerta/teste/guardrail/runbook).

WHEN um sinal **contradiz** a hipótese THEN o sistema SHALL refinar ou descartar a hipótese (não ignorar a contra-evidência).

WHEN a causa é óbvia (fix < 30s, domínio único) THEN o sistema SHALL responder direto, **sem** abrir investigação (evitar overhead).

## Acceptance Criteria

- [ ] Endpoint/intent de investigação distinto do fluxo de query simples (ou flag `mode=investigate`).
- [ ] Coleta de evidência em **paralelo** (reusa fan-out da spec 17), com janela temporal derivada do sintoma.
- [ ] `Evidence` estruturada: `{source_agent, signal_type, timestamp, strength (forte/média/fraca), summary}`.
- [ ] Timeline ordenada por timestamp, com marcação de eventos candidatos a causa (deploy, config change, restart).
- [ ] Correlação: regra "≥3 sinais independentes → confiança alta" implementada e testável.
- [ ] RCA output: `{hypothesis, confidence, evidence[], timeline[], contradicting[], prevention[]}`.
- [ ] Contra-evidência não é descartada silenciosamente (campo `contradicting` + efeito na confiança).
- [ ] Fast-path: sintoma trivial não dispara fan-out (limiar de decisão explícito).
- [ ] Limite de custo: nº de agentes e nº de queries de evidência por investigação configurável (via spec 19).
- [ ] Read-only preservado: investigação só **lê** (nenhuma ação de remediação executada — só proposta).
- [ ] Testes (test-author ≠ autor, ≥90%): coleta paralela, construção de timeline, regra de correlação (3 sinais), tratamento de contra-evidência, fast-path trivial.

## Fora de escopo

- **Remediação automática** (executar o fix) — viola read-only; só propõe.
- Detecção de anomalia proativa (CronJob que abre investigação sozinho) — futuro.
- Memória de incidentes / aprendizado entre investigações → spec `21-incident-memory-learning`.
- Fase 2 (correlação multi-hipótese avançada, fault-tree) — ver promotion triggers em `tasks.md`.
