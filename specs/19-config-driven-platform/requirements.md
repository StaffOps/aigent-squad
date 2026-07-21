---
spec: 19-config-driven-platform
status: superseded
completed: null
superseded_by: "22-agent-capability-manifest"
depends_on: []
deferred: []
---

# Feature: Config-Driven Platform

**Spec**: `19-config-driven-platform`
**Severidade**: 🟠 High (pré-requisito de produto)
**Origem**: requisito do usuário (vira produto → configuração via config file + env); achados de hardcode (`AGENT_URLS`, `mcp_servers`, `PROMETHEUS_URL`, grupos GitLab)
**Depende de**: — (pode começar cedo, em paralelo)

Hoje a configuração está espalhada e parte é hardcoded: `AGENT_URLS` no `supervisor/agent.py`, `mcp_servers` no `config.py`, `PROMETHEUS_URL` no agente de observability, grupos GitLab ("Company") no `gitlab_client`. Pra virar produto, **toda** configuração precisa vir de **arquivo de config declarativo + override por env var**, sem hardcode no código.

## User Stories

WHEN o operador define os agentes (endpoint, modelo, datasources, enable/disable) THEN o sistema SHALL ler isso de um **config file** (YAML), não de constantes no código.

WHEN uma env var correspondente existe THEN ela SHALL **sobrescrever** o valor do config file (precedência: env > file > default).

WHEN um secret é necessário (tokens, passwords) THEN ele SHALL vir de env var ou arquivo montado (file-mounted), **nunca** do config file versionado.

WHEN o supervisor precisa da URL de um agente THEN ela SHALL vir do registry de agentes no config (sem `AGENT_URLS` hardcoded).

WHEN um datasource de observabilidade é usado (Prometheus/Loki/Tempo) THEN seu endpoint SHALL vir do config (sem hardcode).

WHEN a investigação (spec 18) ou o fan-out (spec 17) precisam de limites (max_agents, teto de evidência, timeouts) THEN esses limites SHALL ser configuráveis.

WHEN o config file está ausente ou inválido THEN o sistema SHALL falhar no **startup** com erro acionável (qual chave, o que falta) — não no primeiro request.

## Acceptance Criteria

- [ ] Config file YAML único (ex: `config/aigent.yaml`) com seções: `agents[]`, `datasources`, `bedrock` (modelos por papel), `limits`, `cache`, `storage`.
- [ ] Precedência **env > file > default** implementada e testada.
- [ ] Secrets **fora** do YAML — só env var ou path de arquivo (`*_FILE`); validado (rejeita secret inline).
- [ ] Registry de agentes substitui `AGENT_URLS` (supervisor lê do config).
- [ ] Datasources (Prometheus/Loki/Tempo/Alertmanager) endpoints no config; `PROMETHEUS_URL` hardcoded removido.
- [ ] `mcp_servers`, grupos GitLab, Athena, docs portal → migrados pro config (sem hardcode).
- [ ] `bedrock`: modelo por papel (`classifier` → Haiku, `agent`/`synthesis` → Sonnet) configurável (alinha specs 11/17).
- [ ] `limits`: `max_agents`, timeouts, teto de evidência da investigação — configuráveis.
- [ ] Validação de schema no startup (Pydantic) com mensagem de erro por chave faltante/ inválida.
- [ ] Enable/disable de agente via flag no config (agente desligado não entra no fan-out nem no classifier).
- [ ] `config.example.yaml` + `.env.example` consistentes e documentados.
- [ ] Testes (test-author ≠ autor, ≥90%): precedência env>file>default, rejeição de secret inline, falha de startup com config inválido, registry de agentes, enable/disable.

## Fora de escopo

- Hot-reload de config em runtime (futuro; por ora, restart aplica config).
- Config remoto/centralizado (Consul/AppConfig) — começa com file local + env.
- Migração pra ExternalSecrets/IRSA (specs 12/13/14 cuidam do prod) — aqui só o **contrato** de "secret via env/arquivo".
