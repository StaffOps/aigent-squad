# Design: Config-Driven Platform

## Arquitetura

Um único ponto de carga de config no startup, com precedência **env > file > default**, validado por Pydantic. Substitui as constantes hardcoded espalhadas.

```
config/aigent.yaml  ──┐
env vars            ──┼─▶  load_config()  ─▶  AppConfig (validado)  ─▶  injetado nos componentes
defaults (no schema)──┘        (startup; falha cedo se inválido)
secrets (env / *_FILE) ─────────────────────────────────────────────▶  resolvidos fora do YAML
```

## Estrutura do config file (YAML)

```yaml
# config/aigent.yaml  (sem secrets — só estrutura/endpoints)
bedrock:
  region: us-east-1
  models:
    classifier: anthropic.claude-3-5-haiku-20241022-v1:0   # barato (spec 11)
    agent:      anthropic.claude-sonnet-4-5-20250929-v1:0
    synthesis:  anthropic.claude-sonnet-4-5-20250929-v1:0

agents:                       # registry — substitui AGENT_URLS hardcoded
  - name: aws
    url: http://aws-agent:8001/process
    enabled: true
  - name: kubernetes
    url: http://kubernetes-agent:8002/process
    enabled: true
  # finops / devops / observability ...

datasources:                  # substitui PROMETHEUS_URL etc. hardcoded
  prometheus: http://prometheus.monitoring.svc.cluster.local:9090
  loki:       http://loki-gateway.monitoring:80
  tempo:      http://tempo-gateway.monitoring:80

limits:
  max_agents: 3               # teto do fan-out (spec 17)
  agent_timeout_s: 25
  investigation_evidence_cap: 8   # teto de queries de evidência (spec 18)

storage:
  dynamodb_table: aigent-sessions
  session_ttl_hours: 168
cache:
  redis_host: redis
  redis_port: 6379
  redis_ssl: true
```

Secrets **não** aparecem aqui — `redis_password`, `gitlab_token`, etc. vêm de env (`REDIS_PASSWORD`) ou arquivo (`GITLAB_TOKEN_FILE=/etc/secrets/gitlab`).

## Componentes

| Componente | Responsabilidade | Onde |
|-----------|------------------|------|
| `AppConfig` (Pydantic) | Schema + validação + defaults | `src/core/config.py` (refatorado) |
| `load_config()` | Lê YAML, aplica overrides de env, valida, resolve secrets | `src/core/config.py` |
| `AgentRegistry` | Lista de agentes habilitados + lookup por nome | derivado de `AppConfig.agents` |

## Precedência (env > file > default)

```python
# Pydantic Settings com source customizada:
# 1. defaults no modelo
# 2. YAML file (config_path env ou ./config/aigent.yaml)
# 3. env vars (AIGENT__BEDROCK__MODELS__CLASSIFIER=... usando delimiter __)
# Ordem de prioridade: env  >  yaml  >  default
```

`pydantic-settings` v2 suporta `settings_customise_sources` + `YamlConfigSettingsSource` + nested delimiter — cobre os três níveis sem código manual de merge.

## Secrets (contrato)

- No YAML: **proibido** valor de secret (validador rejeita chaves conhecidas de secret com valor inline).
- Permitido: `redis_password` via env `REDIS_PASSWORD`, ou `*_FILE` apontando pra arquivo montado (lê no startup).
- Alinha com 12-factor / `cloud-security` (file-mounted preferível); a migração pra ExternalSecrets é nas specs 12–14.

## Rationale (decisões e trade-offs)

### Decisão 1: YAML file + env override (não env-only, não config remoto)

**Escolha**: config declarativo em arquivo YAML, com env vars sobrescrevendo, validado no startup.

**Justificativa, em ordem de força**:
1. **Produto precisa de config legível e versionável** — um registry de agentes/datasources em YAML é inspecionável e diffável; env-only (o estado atual com `AGENT_URLS` no código + flags soltas) não escala pra dezenas de chaves.
2. **Env override é table-stakes em K8s** — values por ambiente (DEV/HML/PRD) sobrescrevem o file base sem reconstruir imagem (12-factor III).
3. **Falhar no startup** com config inválido é muito melhor que descobrir no 1º request em produção.

**Trade-offs aceitos**:
| Custo | Realidade |
|-------|-----------|
| Mais um arquivo pra manter | É a fonte única — elimina hardcode espalhado em 4+ lugares |
| `pydantic-settings` nested sources tem curva | Resolve merge/precedência sem código manual; bem documentado |

**Quando estaria errada** (signals): se o nº de instâncias/ambientes crescer a ponto de exigir config centralizado dinâmico → migrar pra AWS AppConfig/Consul (fora de escopo agora).

**Alternativas descartadas**:
- **Env-only** — não escala pra registry de agentes/datasources; ilegível.
- **Config remoto (AppConfig/Consul) já** — complexidade prematura; o usuário pediu "não complexo demais".

### Decisão 2: Registry de agentes no config (mata `AGENT_URLS` hardcoded)

**Escolha**: o supervisor lê a lista de agentes (url, enabled) do config, não de uma constante.

**Justificativa**:
1. **Produtização**: adicionar/remover/desligar um agente vira mudança de config, não de código + rebuild.
2. **Habilita 17/18**: fan-out e investigação iteram sobre "agentes habilitados" — precisa ser dado, não constante.

**Trade-off aceito**: um lookup a mais no startup — irrelevante perto da flexibilidade.

### Decisão 3: Secrets fora do YAML, sempre

**Escolha**: o YAML versionado nunca contém secret; validador rejeita.

**Justificativa**: segurança (`cloud-security`, 12-factor) — secret em arquivo versionado é o anti-pattern clássico. Fail-closed: se alguém puser inline, o startup recusa.

## Invariantes

- Precedência **env > file > default** — sempre.
- Nenhum secret no YAML versionado (validado).
- Config inválido = **falha no startup** (nunca no request).
- Agente `enabled: false` não entra em classifier/fan-out/investigação.
- Zero endpoint/credencial hardcoded no código após esta spec.

## Dependências externas

| Lib | Uso |
|-----|-----|
| `pydantic-settings` v2 | schema + sources (env/yaml) + validação |
| `PyYAML` | parse do config file |

## Verificação

```bash
docker run --rm -v $(pwd):/app -w /app python:3.11-slim sh -c \
  "pip install -q -r requirements.txt pytest && pytest tests/ -v --cov=src --cov-fail-under=90"
```

Testes-chave (test-author ≠ autor): env sobrescreve yaml sobrescreve default; secret inline no yaml → erro de validação; yaml ausente/ inválido → falha no startup com chave indicada; registry retorna só agentes `enabled`; `*_FILE` lê secret de arquivo.

## Riscos

- Migração quebra algo que dependia de hardcode — mitigar fazendo a refatoração com os testes da spec 02 verdes.
- Drift entre `config.example.yaml` e `.env.example` — manter consistência (documentation-sync) e cobrir no `--check` se houver.
