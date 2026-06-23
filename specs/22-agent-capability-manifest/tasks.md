# Tasks: Config-Driven Agent Platform

## Phase A — Core runtime (imagem genérica funcional)

- [x] T1: Criar schema Pydantic `AgentConfig` com validação estrita — done 2026-06-14
- [x] T2: Implementar `AgentRegistry` — discovery de `AGENTS_DIR/<name>/agent.yaml` — done 2026-06-14
- [x] T3: Implementar interface `DatasourceAdapter` + `HttpAdapter` (mais genérico) — done 2026-06-14
- [x] T4: Implementar `Boto3Adapter` (read-only: describe/list/get only) — done 2026-06-14
- [x] T5: Implementar `KubernetesAdapter` — done 2026-06-14
- [x] T6: Implementar `AthenaAdapter` — done 2026-06-14
- [x] T7: Implementar `GenericAgent` — fluxo unificado (validate → cache → adapters → prompt → bedrock → respond) — done 2026-06-14
- [x] T8: Migrar os 5 agentes atuais para format `agents/<name>/agent.yaml + prompt.md` — done 2026-06-14
- [x] T9: Atualizar Classifier para consumir registry (remover lista hardcoded) — done 2026-06-14
- [x] T10: Atualizar Supervisor para descobrir URLs do registry — done 2026-06-14
- [x] T11: Unificar em 1 Dockerfile (GenericAgent + todos adapters) — done 2026-06-14
- [x] T12: Atualizar docker-compose para usar imagem única + AGENTS_DIR volume mount — done 2026-06-14
- [x] T13: Testes ≥90% (discovery, adapters, GenericAgent, classifier, startup failure) — done 2026-06-14

## Phase B — Helm chart (deploy customizável)

- [x] T14: Criar Helm chart com template loop (`agents[]` → N Deployments) — done 2026-06-14
- [x] T15: Suporte a `agentsSource: configmap` (gera ConfigMap por agent dir) — done 2026-06-14
- [x] T16: Suporte a `agentsSource: git` (initContainer com git clone) — done 2026-06-14
- [x] T17: Exemplo: adicionar 6º agente ("security") só via config (demonstrar zero-code) — done 2026-06-14
- [x] T18: Docs: README + HOW-TO "Creating a new agent" — done 2026-06-14

## Ordem sugerida

T1 → T2 → T3/T4/T5/T6 (paralelo) → T7 → T8 → T9/T10 → T11/T12 → T13
Phase B depende de Phase A completa.

## Notas

- Phase A é o redesign de produto. Após T12, o docker-compose roda com a arquitetura nova.
- Phase B é deploy K8s. Pode ser feita depois das specs 03/04 se preferir.
- A migração (T8) preserva os prompts e comportamento atuais — é lift-and-shift, não rewrite.
- Adapters que não existem no agent.yaml de nenhum agente não são instanciados (zero overhead).

## Status (2026-06-14)

**Completed**: Phase A (T1–T13) AND Phase B (T14–T18). Full config-driven platform with GenericAgent, adapter pattern (Http, Boto3, Kubernetes, Athena), agent registry, unified Docker image, Helm chart with configmap+git source modes, 6th agent demo (security), and HOW-TO documentation.

**Key deliverables**:
- Single Docker image serving all agents via `AGENTS_DIR` volume mount
- Helm chart with `agentsSource: configmap` and `agentsSource: git` modes
- Zero-code agent addition demonstrated with security agent
- Classifier consumes registry dynamically (no hardcoded list)
- This spec **substitutes spec 19** (config-driven-platform)

**Deferred (future refinement)**:
- ExternalSecret integration in Helm chart (requires AWS infra — spec 12)
- NetworkPolicy in Helm chart (requires K8s deployment — spec 14)
- Specialized adapters (GitLabAdapter, RAGAdapter) listed in backlog
