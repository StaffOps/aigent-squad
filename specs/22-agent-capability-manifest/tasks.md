# Tasks: Config-Driven Agent Platform

## Phase A — Core runtime (functional generic image)

- [x] T1: Create Pydantic schema `AgentConfig` with strict validation — done 2026-06-14
- [x] T2: Implement `AgentRegistry` — discovery of `AGENTS_DIR/<name>/agent.yaml` — done 2026-06-14
- [x] T3: Implement interface `DatasourceAdapter` + `HttpAdapter` (most generic) — done 2026-06-14
- [x] T4: Implement `Boto3Adapter` (read-only: describe/list/get only) — done 2026-06-14
- [x] T5: Implement `KubernetesAdapter` — done 2026-06-14
- [x] T6: Implement `AthenaAdapter` — done 2026-06-14
- [x] T7: Implement `GenericAgent` — unified flow (validate → cache → adapters → prompt → bedrock → respond) — done 2026-06-14
- [x] T8: Migrate the 5 current agents to format `agents/<name>/agent.yaml + prompt.md` — done 2026-06-14
- [x] T9: Update Classifier to consume registry (remove hardcoded list) — done 2026-06-14
- [x] T10: Update Supervisor to discover URLs from registry — done 2026-06-14
- [x] T11: Unify into 1 Dockerfile (GenericAgent + all adapters) — done 2026-06-14
- [x] T12: Update docker-compose to use single image + AGENTS_DIR volume mount — done 2026-06-14
- [x] T13: Tests ≥90% (discovery, adapters, GenericAgent, classifier, startup failure) — done 2026-06-14

## Phase B — Helm chart (customizable deploy)

- [x] T14: Create Helm chart with template loop (`agents[]` → N Deployments) — done 2026-06-14
- [x] T15: Support `agentsSource: configmap` (generates ConfigMap per agent dir) — done 2026-06-14
- [x] T16: Support `agentsSource: git` (initContainer with git clone) — done 2026-06-14
- [x] T17: Example: add 6th agent ("security") via config only (demonstrate zero-code) — done 2026-06-14
- [x] T18: Docs: README + HOW-TO "Creating a new agent" — done 2026-06-14

## Suggested order

T1 → T2 → T3/T4/T5/T6 (parallel) → T7 → T8 → T9/T10 → T11/T12 → T13
Phase B depends on Phase A being complete.

## Notes

- Phase A is the product redesign. After T12, docker-compose runs with the new architecture.
- Phase B is K8s deploy. Can be done after specs 03/04 if preferred.
- The migration (T8) preserves the current prompts and behavior — it is a lift-and-shift, not a rewrite.
- Adapters that do not exist in any agent's agent.yaml are not instantiated (zero overhead).

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
