---
spec: 22-agent-capability-manifest
status: done
completed: null
superseded_by: null
depends_on: []
deferred: []
---

# Feature: Config-Driven Agent Platform

**Spec**: `22-agent-capability-manifest`
**Severidade**: 🟠 High (redesign arquitetural — habilita produto customizável)
**Substitui**: spec 19 (`config-driven-platform`)
**Visão de produto**: 1 imagem genérica + N agentes definidos por diretório (YAML + prompt.md). Quem deploya escolhe quantos e quais agentes quer via Helm values, sem escrever código.

---

## Conceito

Um agente **não é código** — é um **diretório de configuração**:

```
agents/
├── aws/
│   ├── agent.yaml      # datasources, capabilities, cache, model
│   └── prompt.md       # system prompt (pode ser longo)
├── finops/
│   ├── agent.yaml
│   ├── prompt.md
│   └── examples/       # few-shot, RAG docs, referências
│       └── cost-patterns.md
└── custom-team-x/
    ├── agent.yaml
    └── prompt.md
```

A imagem genérica **auto-descobre** o diretório no startup: cada subdir com `agent.yaml` vira um agente funcional.

---

## User Stories

WHEN o operador aponta `AGENTS_DIR` para um diretório THEN o sistema SHALL descobrir todos os subdirs com `agent.yaml` e registrar um agente por cada.

WHEN o operador adiciona um novo subdir com `agent.yaml` + `prompt.md` e reinicia THEN o sistema SHALL disponibilizar o novo agente ao classifier **sem mudança de código ou rebuild de imagem**.

WHEN o Helm chart é deployado com `agents[]` no values.yaml THEN cada entrada SHALL gerar um Deployment + Service + ConfigMap com a config do agente.

WHEN o `agent.yaml` declara `datasources` THEN o runtime SHALL instanciar os adapters correspondentes e injetá-los no agente.

WHEN o `prompt.md` ultrapassa 100 linhas THEN ele SHALL estar isolado em arquivo (não inline no YAML), evitando poluição.

WHEN o classifier roteia uma query THEN ele SHALL usar `name` + `description` + `capabilities` + `routing_keywords` dos manifestos (não uma lista hardcoded).

WHEN o `agent.yaml` é inválido (campo obrigatório ausente, datasource type desconhecido) THEN o sistema SHALL falhar no **startup** com erro acionável.

WHEN dois agentes declaram a mesma capability THEN o classifier SHALL desempatar por `routing_keywords` e `domain`.

WHEN `read_only: true` THEN o sistema SHALL preservar essa invariante em todos os caminhos.

WHEN `enabled: false` THEN o agente SHALL ser ignorado no discovery.

---

## Acceptance Criteria

- [ ] 1 imagem Docker genérica que roda qualquer agente baseado em config.
- [ ] Auto-descoberta de `AGENTS_DIR/<name>/agent.yaml` no startup.
- [ ] Schema Pydantic para `agent.yaml` com validação estrita.
- [ ] Registry de datasource adapters: `boto3`, `kubernetes`, `http`, `prometheus`, `athena`, `gitlab`.
- [ ] `prompt.md` carregado do mesmo diretório do `agent.yaml`.
- [ ] Classifier consome o registry (lista de agentes é dinâmica, não hardcoded).
- [ ] Supervisor/coordinator descobre agentes pelo registry (não por URLs fixas).
- [ ] Helm chart gera N deployments a partir de `agents[]` no values.
- [ ] Exemplo funcional: 5 agentes atuais migrados para o formato config + 1 agente novo demonstrando extensibilidade.
- [ ] Falha de startup com config inválida (schema, datasource desconhecido, env ausente).
- [ ] `read_only` honrado como invariante de segurança.
- [ ] Testes ≥90%: discovery, seleção por capability, falha de startup, adapter instantiation.

---

## Fora de escopo

- Hot-reload sem restart (futuro — restart é aceitável para MVP).
- Git-sync automático no cluster (initContainer é suficiente por ora).
- Marketplace/versionamento de manifestos.
- Datasource adapters além dos 6 listados (plugáveis via interface, mas não implementados agora).
- Multi-tenant (cada tenant com agents diferentes) — futuro.

---

## Fonte do diretório (deploy-time, não runtime)

| Fonte | Como montar | Quando usar |
|-------|-------------|-------------|
| Local (dev) | `docker run -v ./agents:/config/agents` | Desenvolvimento local |
| ConfigMap | Helm gera ConfigMap por agent, monta em volume | Deploy simples, tudo no Helm |
| Git repo | initContainer + git clone + shared volume | Produção, GitOps-native |
| S3/bucket | initContainer baixa .tar.gz | Agents atualizáveis sem redeploy |

A plataforma só sabe ler de um diretório. A **fonte** é configuração de deploy (Helm values), não de código.
