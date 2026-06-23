# Tasks: Config-Driven Platform

> Pode começar cedo (sem dependência). Habilita specs 17 (max_agents), 18 (datasources/limites), 11 (modelo por papel).

- [ ] T1: Definir schema `AppConfig` (Pydantic) — seções `bedrock.models`, `agents[]`, `datasources`, `limits`, `storage`, `cache`
- [ ] T2: `load_config()` com sources env>file>default (`pydantic-settings` v2 + `YamlConfigSettingsSource`, nested delimiter `__`) (depends on: T1)
- [ ] T3: Validação de secret — rejeitar valor inline de chave de secret no YAML; suportar env e `*_FILE` (depends on: T1)
- [ ] T4: `AgentRegistry` derivado de `AppConfig.agents` (lookup + só `enabled`); remover `AGENT_URLS` do supervisor (depends on: T2)
- [ ] T5: Migrar datasources — `PROMETHEUS_URL` e afins → `config.datasources`; remover hardcode do observability agent (depends on: T2)
- [ ] T6: Migrar `mcp_servers`, grupos GitLab ("Company"), Athena, docs portal → config (sem hardcode) (depends on: T2)
- [ ] T7: Modelo Bedrock por papel (`classifier`/`agent`/`synthesis`) lido do config (alinha specs 11/17) (depends on: T2)
- [ ] T8: `limits` (`max_agents`, timeouts, `investigation_evidence_cap`) lidos do config (depends on: T2)
- [ ] T9: Falha no startup com config inválido — mensagem por chave faltante/ inválida (depends on: T2, T3)
- [ ] T10: `config/aigent.example.yaml` + `.env.example` consistentes + doc (README seção config) (depends on: T1–T8)
- [ ] T11 (test-author DIFERENTE do autor): pytest ≥90% — precedência env>file>default, rejeição de secret inline, falha de startup, registry só-enabled, `*_FILE` (depends on: T9)
- [ ] T12: Review independente (`code-review`): zero hardcode remanescente, secrets fora do YAML, precedência correta (depends on: T11)

## Ordem sugerida
T1→T2→T3; T4/T5/T6/T7/T8 em paralelo (após T2); T9; T10; T11→T12.

## Notas
- Precedência env>file>default é invariante — testar explicitamente.
- Secret no YAML versionado = falha (fail-closed).
- Esta spec NÃO faz hot-reload nem config remoto (fora de escopo).
- Pipeline de verificação (`verification-independence.md`): T1–T10 autor; T11 test-author em sessão diferente; T12 code-review.
- Per `documentation-sync`: atualizar README com a seção de configuração na mesma mudança.
