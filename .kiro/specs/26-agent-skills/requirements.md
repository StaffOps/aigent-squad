# Feature: Agent Skills (lazy-loaded knowledge)

## Contexto

Hoje, conhecimento especializado de um agente vive **inline no `prompt.md`**
(o do kubernetes já tem ~8KB). Isso não escala e não permite **reuso entre
agentes** (um guia de "investigar OOMKill" serve kubernetes E observability).

"Skill" aqui = **conhecimento on-demand em markdown** (sentido do
`staffops_agent_definition` SKILL.md), NÃO uma ação/ferramenta (isso já são
os `datasources`/adapters) nem RAG dinâmico (isso é o módulo `kb/`).

## User Stories

WHEN um agente processa uma query cujo tema casa com uma skill disponível
THEN o sistema SHALL injetar o conteúdo daquela skill no system prompt antes
de chamar o Bedrock.

WHEN nenhuma skill casa com a query
THEN o sistema SHALL NÃO injetar skill alguma (lazy — economiza tokens).

WHEN uma skill é referenciada por múltiplos agentes
THEN ela SHALL viver em um diretório global compartilhado (`skills/`), não
duplicada por agente.

WHEN um arquivo de skill referenciado não existe ou está corrompido
THEN o carregamento SHALL degradar graciosamente (fail-open: ignora a skill,
loga warning, não derruba o agente).

## Acceptance Criteria

- [ ] Diretório global `skills/<name>/SKILL.md` com frontmatter YAML
      (`name`, `description`, `keywords`).
- [ ] Campo `skills: [<name>, ...]` no `agent.yaml` declara quais skills o
      agente PODE usar (allowlist por agente).
- [ ] Seleção **lazy**: a skill só entra no prompt quando a query casa seus
      `keywords` (ou descrição). Sem match ⇒ não injeta.
- [ ] Skills injetadas num bloco `<skills>` do system prompt, tratado como
      conhecimento (não instrução executável — mantém defesa de prompt
      injection já existente).
- [ ] Fail-open: skill ausente/inválida não quebra o agente.
- [ ] Cobertura de testes ≥90% no código novo.

## Fora de escopo

- Tool-loop / skill que executa código (isso é datasource/adapter — ver ADR-001).
- RAG dinâmico (já coberto por `src/core/kb/`).
- Skill com embeddings/semantic match (Fase 2 — começar com keyword match).
- UI de gerenciamento de skills.
