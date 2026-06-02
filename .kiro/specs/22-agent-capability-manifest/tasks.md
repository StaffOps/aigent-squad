# Tasks: Agent Capability Manifest

> Estende o `AgentRegistry`/`agent-extensibility` do `staffops-chaitops`. Substitui a spec 19. Habilita 17 (seleção multi-agente) e 18 (evidence_types).

- [ ] T1: Schema Pydantic do manifesto (`name`, `description`, `domain`, `capabilities[]`, `routing_keywords[]`, `datasources[]`, `evidence_types[]`, `delegates_to[]`, `read_only`, `model_tier`, `required_env[]`, `sidecar_url`, `enabled`)
- [ ] T2: Discovery `config/agents/*.yaml` no startup + validação (campos obrigatórios) (depends on: T1)
- [ ] T3: Validação de `delegates_to` — destino existe no roster; bloquear ciclo direto A→B→A (depends on: T2)
- [ ] T4: Classifier consome `name`/`description`/`capabilities`/`routing_keywords` (remove lista hardcoded de agentes) (depends on: T2)
- [ ] T5: Seleção multi-agente por `capabilities`/`domain` exposta ao coordinator (alinha spec 17) (depends on: T4)
- [ ] T6: Expor `evidence_types` ao fluxo de RCA (alinha spec 18) (depends on: T2)
- [ ] T7: `model_tier` → modelo Bedrock por papel (alinha specs 11/19); `read_only` honrado no roteamento (depends on: T2)
- [ ] T8: Manifestos `*.example.yaml` para os 5 especialistas atuais + 1 novo (provar roster aberto) (depends on: T1)
- [ ] T9 (test-author DIFERENTE do autor): pytest ≥90% — discovery, seleção por capability, delegates_to órfão/ciclo, roster aberto, falha de startup, read_only (depends on: T5, T6, T7)
- [ ] T10: Review independente (`code-review`): zero lista hardcoded, validação de delegates_to, invariante read_only (depends on: T9)
- [ ] T11: Doc — README seção "Adicionar um agente" (só manifesto) + tabela de campos (depends on: T8)

## Ordem sugerida
T1→T2→T3; T4→T5; T6; T7; T8; T9→T10→T11.

## Notas
- Reusar o `AgentRegistry` do chaitops como base (não reescrever discovery/`required_env`/`/ready`).
- `delegates_to` é a peça de "como os agentes se ajudam" — declarativo, validado, revisável em PR.
- Pipeline de verificação (`verification-independence.md`): T1–T8/T11 autor; T9 test-author em sessão diferente; T10 code-review.
- Per `documentation-sync`: README ganha a seção de extensibilidade na mesma mudança.
