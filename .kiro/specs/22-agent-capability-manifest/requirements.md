# Feature: Agent Capability Manifest

**Spec**: `22-agent-capability-manifest`
**Severidade**: 🟠 High (mecanismo central — habilita roster aberto + colaboração)
**Origem**: requisito do usuário (não limitar a 5 agentes; colaboração via nome/descrição + metadados úteis)
**Relação**: estende o `AgentRegistry`/`agent-extensibility` do `staffops-chaitops` (ver `ECOSYSTEM.md`) e o `specialists.yaml` do `multi-agent-coordinator`. Substitui a nossa spec `19-config-driven-platform` (registry plano) por um registry **capability-rich**.

O roster de especialistas é **aberto e declarativo**: adicionar um agente = adicionar um manifesto YAML (zero código). Cada manifesto descreve o agente com metadados suficientes para que o classifier/coordinator saibam **quando** acioná-lo, **o que** ele acessa, e **como** os agentes se ajudam entre si — sem matriz de colaboração hardcoded.

## User Stories

WHEN o operador adiciona um manifesto de agente em `config/agents/<name>.yaml` THEN o sistema SHALL descobri-lo no startup e disponibilizá-lo ao classifier/coordinator **sem mudança de código**.

WHEN o classifier decide o roteamento THEN ele SHALL usar `name` + `description` + `capabilities` + `routing_keywords` dos manifestos (não uma lista fixa no código).

WHEN uma query é cross-domain THEN o coordinator SHALL selecionar o **conjunto** de agentes cujas `capabilities`/`domains` casam (1..N), não apenas um.

WHEN um agente precisa de ajuda fora do seu domínio THEN ele SHALL consultar o campo `delegates_to` do próprio manifesto (par `agent` + `when`) para saber a quem pedir — colaboração **dirigida por dados**, não hardcoded.

WHEN uma investigação de RCA coleta evidência THEN o coordinator SHALL usar `evidence_types` de cada manifesto para saber **que tipo de evidência** cada agente contribui.

WHEN um agente é marcado `read_only: true` THEN o sistema SHALL preservar essa invariante (nunca rotear uma ação mutante para ele).

WHEN dois agentes declaram o mesmo `capability` THEN o classifier SHALL desempatar por `routing_keywords`, `domain` e (se houver) `priority`.

WHEN um manifesto é inválido (campo obrigatório ausente, `delegates_to` aponta para agente inexistente) THEN o sistema SHALL falhar no **startup** com erro acionável.

## Acceptance Criteria

- [ ] Schema de manifesto (Pydantic) com: `name`, `description`, `domain`, `capabilities[]`, `routing_keywords[]`, `datasources[]`, `evidence_types[]`, `delegates_to[]` (`agent`+`when`), `read_only`, `model_tier`, `required_env[]`, `endpoint`/`sidecar_url`, `enabled`.
- [ ] Auto-descoberta de `config/agents/*.yaml` no startup (estende o `AgentRegistry` do chaitops).
- [ ] Classifier/coordinator consomem os manifestos (lista de agentes deixa de ser hardcoded no código).
- [ ] Seleção multi-agente por `capabilities`/`domain` (não 1-fixo) — alinha com a spec 17.
- [ ] `delegates_to` resolvido e **validado** contra o conjunto de agentes (sem destino órfão; sem ciclo trivial A→B→A direto).
- [ ] `evidence_types` exposto ao fluxo de RCA (spec 18) para montar a coleta de evidência.
- [ ] `read_only` respeitado como invariante de segurança.
- [ ] `model_tier` (`fast`/`standard`/`premium`) → mapeia para o modelo Bedrock por papel (alinha specs 11/19).
- [ ] Roster **aberto**: adicionar/remover/desligar agente é só manifesto (sem rebuild).
- [ ] Falha de startup com manifesto inválido ou `delegates_to` órfão.
- [ ] `config/agents/*.example.yaml` para os especialistas atuais + ≥1 novo (demonstrar extensibilidade).
- [ ] Testes (test-author ≠ autor, ≥90%): descoberta, seleção por capability, resolução/validação de `delegates_to`, roster aberto, falha de startup, invariante read-only.

## Fora de escopo

- Descoberta dinâmica em runtime (hot-reload) — restart aplica o manifesto novo.
- Aprendizado automático de `delegates_to` (LLM inferindo colaboração) — por ora é declarativo.
- Implementar os agentes novos em si — esta spec define o **contrato/registry**; novos agentes entram sob demanda.
- Marketplace/versionamento de manifestos — futuro.
