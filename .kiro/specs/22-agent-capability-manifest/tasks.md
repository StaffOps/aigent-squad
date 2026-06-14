# Tasks: Config-Driven Agent Platform

## Phase A — Core runtime (imagem genérica funcional)

- [ ] T1: Criar schema Pydantic `AgentConfig` com validação estrita
- [ ] T2: Implementar `AgentRegistry` — discovery de `AGENTS_DIR/<name>/agent.yaml`
- [ ] T3: Implementar interface `DatasourceAdapter` + `HttpAdapter` (mais genérico)
- [ ] T4: Implementar `Boto3Adapter` (read-only: describe/list/get only)
- [ ] T5: Implementar `KubernetesAdapter`
- [ ] T6: Implementar `AthenaAdapter`
- [ ] T7: Implementar `GenericAgent` — fluxo unificado (validate → cache → adapters → prompt → bedrock → respond)
- [ ] T8: Migrar os 5 agentes atuais para format `agents/<name>/agent.yaml + prompt.md`
- [ ] T9: Atualizar Classifier para consumir registry (remover lista hardcoded)
- [ ] T10: Atualizar Supervisor para descobrir URLs do registry
- [ ] T11: Unificar em 1 Dockerfile (GenericAgent + todos adapters)
- [ ] T12: Atualizar docker-compose para usar imagem única + AGENTS_DIR volume mount
- [ ] T13: Testes ≥90% (discovery, adapters, GenericAgent, classifier, startup failure)

## Phase B — Helm chart (deploy customizável)

- [ ] T14: Criar Helm chart com template loop (`agents[]` → N Deployments)
- [ ] T15: Suporte a `agentsSource: configmap` (gera ConfigMap por agent dir)
- [ ] T16: Suporte a `agentsSource: git` (initContainer com git clone)
- [ ] T17: Exemplo: adicionar 6º agente ("security") só via config (demonstrar zero-code)
- [ ] T18: Docs: README + HOW-TO "Creating a new agent"

## Ordem sugerida

T1 → T2 → T3/T4/T5/T6 (paralelo) → T7 → T8 → T9/T10 → T11/T12 → T13
Phase B depende de Phase A completa.

## Notas

- Phase A é o redesign de produto. Após T12, o docker-compose roda com a arquitetura nova.
- Phase B é deploy K8s. Pode ser feita depois das specs 03/04 se preferir.
- A migração (T8) preserva os prompts e comportamento atuais — é lift-and-shift, não rewrite.
- Adapters que não existem no agent.yaml de nenhum agente não são instanciados (zero overhead).
