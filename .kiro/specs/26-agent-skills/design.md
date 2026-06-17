# Design: Agent Skills (lazy-loaded knowledge)

## Arquitetura

```
skills/                              ← global, compartilhado entre agentes
└── <skill-name>/
    └── SKILL.md                     ← frontmatter YAML + corpo markdown

agents/<agent>/agent.yaml
└── skills: [<skill-name>, ...]      ← allowlist por agente

Fluxo (lazy, em process_request):
  query ──▶ SkillRegistry.select(allowlist, query)
              │  (keyword match contra frontmatter)
              ▼
        skills relevantes ──▶ injeta <skills> no system prompt ──▶ Bedrock
```

## Componentes

| Componente | Responsabilidade |
|-----------|-----------------|
| `SKILL.md` | Conhecimento + frontmatter (`name`, `description`, `keywords`) |
| `SkillRegistry` | Descobre/parseia skills de `skills/` no startup (1x) |
| `AgentConfig.skills` | Allowlist: quais skills o agente pode usar |
| `GenericAgent` | No `process_request`: seleciona (lazy) + injeta no prompt |

## Formato do SKILL.md

```markdown
---
name: oomkill-investigation
description: How to investigate OOMKilled pods
keywords: [oomkill, oom, memory, killed, evicted, restart]
---

# Investigating OOMKilled Pods
... conhecimento ...
```

## Rationale (decisões)

### Decisão 1: Skill é knowledge estático, não ferramenta

**Escolha**: skill injeta texto no prompt; não executa nada.

**Justificativa, em ordem de força**:
1. Mantém a invariante read-only/consultiva (ADR-001). Skill-como-ferramenta
   reintroduziria o tool-loop que decidimos NÃO adotar.
2. Ação/capacidade já tem mecanismo: `datasources`/adapters (boto3, mcp, etc).
   Criar um segundo caminho pra "fazer coisa" seria redundante e confuso.
3. Knowledge estático é trivialmente testável e cacheável.

**Trade-offs aceitos**:
| Custo | Realidade |
|-------|-----------|
| Skill não pode "buscar" dado novo | Pra isso existe adapter/RAG — separação clara de papéis |

**Quando estaria errada**: se skills precisarem de parâmetros dinâmicos ou
chamar APIs — aí vira adapter, não skill.

### Decisão 2: Seleção lazy por keyword match (não eager, não embeddings)

**Escolha**: skill entra no prompt só quando a query casa `keywords`; match
por palavra-chave simples (não semantic/embeddings) na v1.

**Justificativa, em ordem de força**:
1. **Token economy**: o pedido explícito foi lazy. Injetar todas as skills
   sempre inflaria o prompt (custo por query) — exatamente o que evitar.
2. Keyword match é determinístico, sem custo de inferência, sem dependência
   nova. Embeddings adicionariam latência + a stack do `kb/` por ganho
   incerto na v1.
3. Reusa o padrão já existente de `routing_keywords` do classifier — mesma
   mecânica mental pro operador.

**Trade-offs aceitos**:
| Custo | Realidade |
|-------|-----------|
| Keyword match erra sinônimos não listados | Operador cura `keywords`; barato de ajustar. Semantic match é promotion trigger Fase 2 |
| Query sem keyword conhecida não traz skill | Aceitável: fail-open, agente responde sem a skill (como hoje) |

**Quando estaria errada** (signal Fase 2): se a taxa de "skill relevante não
injetada por falta de keyword" for alta em uso real → migrar pra embeddings
(reusando `kb/embedder.py`).

**Limitação conhecida (validada na implementação)**: o match é por **token
exato**, então flexões não são cobertas automaticamente (`oomkill` não casa
`oomkilled`). O operador deve listar as formas relevantes nos `keywords`
(ex: `[oomkill, oomkilled, oom]`). Isso é barato de ajustar e mantém o match
determinístico; stemming/embeddings ficam para a Fase 2 se a curadoria manual
se mostrar insuficiente.

### Decisão 3: Skills globais + allowlist por agente

**Escolha**: arquivos em `skills/` global; cada `agent.yaml` lista quais pode usar.

**Justificativa**:
1. Reuso (pedido explícito): um `oomkill.md` serve kubernetes + observability
   sem duplicar.
2. Allowlist por agente evita poluir um agente com skill de outro domínio
   (o finops não precisa de TraceQL).

**Trade-offs aceitos**:
| Custo | Realidade |
|-------|-----------|
| Duas fontes (skill global + allowlist local) | Padrão idêntico ao de datasources; operador já conhece |

## Invariantes

- Skill nunca executa código — só injeta texto.
- Conteúdo de skill entra como DADO no prompt (dentro de tag), sob a mesma
  defesa de prompt injection do `<infra_data>`.
- Fail-open: skill ausente/inválida → warning, segue sem ela.

## Integração com o fluxo atual

`SkillRegistry` carrega no startup (junto do `AgentRegistry`). O
`GenericAgent.process_request` ganha um passo antes de montar o contexto:
seleciona skills da allowlist que casam a query e as concatena ao
`system_prompt`. Zero mudança no `bedrock.py`.
